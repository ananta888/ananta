"""Revision-safe Kanban projection of the existing hub task system."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Protocol

from agent.common.audit import log_audit
from agent.db_models.tasks import TaskDB
from agent.repositories.kanban_projection import (
    KanbanIdempotencyConflict,
    KanbanRevisionConflict,
    KanbanScope,
    KanbanTaskNotFound,
    SqlKanbanProjectionStore,
)
from agent.services.kanban_authorization_service import (
    KanbanAuthorizationError,
    KanbanAuthorizationService,
    KanbanPrincipal,
)
from agent.services.kanban_board_projection import (
    COLUMN_TARGET,
    decode_kanban_cursor_offset,
    encode_kanban_cursor,
    kanban_board_revision,
    kanban_card_assignee,
    kanban_column,
    kanban_history_event_type,
    kanban_sort_key,
    ordered_kanban_tasks,
    project_kanban_board,
    project_kanban_cards,
    require_kanban_page_limit,
)
from agent.services.kanban_event_stream_service import (
    build_kanban_event,
    get_kanban_event_stream_service,
)
from agent.services.kanban_mutation_rules import (
    KanbanMutation,
    kanban_mutation_fingerprint,
    kanban_store_error,
    rank_kanban_tasks,
    record_kanban_history_event,
    require_acyclic_kanban_dependencies,
    require_kanban_transition,
)
from agent.services.kanban_service_error import KanbanServiceError
from agent.services.kanban_vector_task_boundary import (
    require_kanban_mutation_allowed,
)
from agent.services.recovery_task_mutation_policy import (
    RecoveryTaskMutationConflict,
    ensure_external_recovery_mutation_allowed,
)
from agent.services.task_runtime_service import notify_task_update
from ananta_contracts.kanban import (
    AssignCardCommand,
    BlockCardCommand,
    CommentCardCommand,
    CompleteCardCommand,
    CreateBoardCommand,
    CreateCardCommand,
    KanbanActivity,
    KanbanActivityPage,
    KanbanBoard,
    KanbanBoardPage,
    KanbanBoardSummary,
    KanbanCapabilities,
    KanbanCapability,
    KanbanCard,
    KanbanCardPage,
    KanbanColumnId,
    KanbanComment,
    KanbanCommentPage,
    KanbanScopeType,
    KanbanSnapshot,
    MoveCardCommand,
    SetDependenciesCommand,
)
from ananta_contracts.kanban_events import KanbanEvent


class KanbanEventPort(Protocol):
    def publish(self, *, action: str, task: TaskDB, actor_id: str, details: dict[str, Any]) -> None: ...


class HubKanbanEventAdapter:
    def publish(self, *, action: str, task: TaskDB, actor_id: str, details: dict[str, Any]) -> None:
        log_audit(
            action,
            {
                "task_id": task.id,
                "actor_id": actor_id,
                "kanban_revision": int(task.kanban_revision or 0),
                **details,
            },
        )
        notify_task_update(task.id)


class KanbanCommittedEventMirrorPort(Protocol):
    def mirror(self, event: KanbanEvent) -> None: ...


class HubKanbanCommittedEventMirror:
    def mirror(self, event: KanbanEvent) -> None:
        get_kanban_event_stream_service().mirror(event)


class KanbanProjectionService:
    def __init__(
        self,
        store: SqlKanbanProjectionStore | None = None,
        authorization: KanbanAuthorizationService | None = None,
        events: KanbanEventPort | None = None,
        event_mirror: KanbanCommittedEventMirrorPort | None = None,
    ):
        default_events = events is None
        self._store = store or SqlKanbanProjectionStore()
        self._auth = authorization or KanbanAuthorizationService()
        self._events = events or HubKanbanEventAdapter()
        self._event_mirror = (
            event_mirror
            if event_mirror is not None
            else HubKanbanCommittedEventMirror() if default_events else None
        )

    @staticmethod
    def parse_scope(board_id: str) -> KanbanScope:
        if board_id == "hub":
            return KanbanScope("hub")
        kind, separator, scope_id = board_id.partition(":")
        if separator and kind in {"goal", "team"} and scope_id:
            return KanbanScope(kind, scope_id)
        raise KanbanServiceError("kanban_board_not_found", "board was not found", status_code=404)

    def _scope(
        self,
        board_id: str,
        principal: KanbanPrincipal,
        capability: KanbanCapability,
    ) -> tuple[KanbanScope, Any | None, Any | None]:
        try:
            self._auth.require_capability(principal, capability)
        except KanbanAuthorizationError as exc:
            raise KanbanServiceError("kanban_forbidden", str(exc), status_code=403) from exc
        scope = self.parse_scope(board_id)
        goal = self._store.get_goal(scope.scope_id) if scope.kind == "goal" else None
        team = self._store.get_team(scope.scope_id) if scope.kind == "team" else None
        if (scope.kind == "goal" and goal is None) or (scope.kind == "team" and team is None):
            raise KanbanServiceError("kanban_board_not_found", "board was not found", status_code=404)
        try:
            self._auth.require_scope(principal, scope.kind, goal=goal, team=team)
        except KanbanAuthorizationError as exc:
            raise KanbanServiceError(
                "kanban_board_not_found", "board was not found", status_code=404
            ) from exc
        return scope, goal, team

    def capabilities(self, principal: KanbanPrincipal, board_id: str | None = None) -> KanbanCapabilities:
        capabilities = self._auth.capabilities_for(principal)
        if board_id:
            try:
                self._scope(board_id, principal, KanbanCapability.READ)
            except KanbanServiceError:
                capabilities = frozenset()
        return KanbanCapabilities(
            board_id=board_id,
            capabilities=tuple(sorted(capabilities, key=lambda item: item.value)),
        )

    _column = staticmethod(kanban_column)
    _sort_key = staticmethod(kanban_sort_key)

    def _ordered(self, tasks: Iterable[TaskDB]) -> list[TaskDB]:
        return ordered_kanban_tasks(tasks)

    _revision = staticmethod(kanban_board_revision)
    _event_type = staticmethod(kanban_history_event_type)
    _assignee = staticmethod(kanban_card_assignee)

    def _cards(self, scope: KanbanScope, tasks: list[TaskDB]) -> list[KanbanCard]:
        return project_kanban_cards(scope, tasks)

    def _board(
        self,
        scope: KanbanScope,
        tasks: list[TaskDB],
        principal: KanbanPrincipal,
        goal: Any | None = None,
        team: Any | None = None,
    ) -> KanbanBoard:
        return project_kanban_board(
            scope,
            tasks,
            capabilities=tuple(sorted(self._auth.capabilities_for(principal), key=lambda item: item.value)),
            goal=goal,
            team=team,
        )

    _cursor = staticmethod(encode_kanban_cursor)
    _offset = staticmethod(decode_kanban_cursor_offset)
    _limit = staticmethod(require_kanban_page_limit)

    def list_boards(
        self, principal: KanbanPrincipal, *, limit: int = 50, cursor: str | None = None
    ) -> KanbanBoardPage:
        self._limit(limit)
        candidates: list[tuple[KanbanScope, Any, Any]] = []
        if principal.is_admin or principal.role.lower() == "admin":
            candidates.append((KanbanScope("hub"), None, None))
        candidates.extend(
            (KanbanScope("goal", str(goal.id)), goal, None)
            for goal in self._store.list_goals()
            if self._auth.can_access_scope(principal, "goal", goal=goal)
        )
        candidates.extend(
            (KanbanScope("team", str(team.id)), None, team)
            for team in self._store.list_teams()
            if self._auth.can_access_scope(principal, "team", team=team)
        )
        boards = [
            KanbanBoardSummary(
                **self._board(scope, self._store.list_tasks(scope), principal, goal, team).model_dump(
                    exclude={"columns"}
                )
            )
            for scope, goal, team in candidates
        ]
        boards.sort(key=lambda item: item.id)
        revision = hashlib.sha256(
            "|".join(f"{item.id}:{item.revision}" for item in boards).encode()
        ).hexdigest()[:24]
        offset = self._offset(cursor, revision)
        page = boards[offset : offset + limit]
        next_cursor = self._cursor(offset + len(page), revision) if offset + len(page) < len(boards) else None
        return KanbanBoardPage(items=tuple(page), next_cursor=next_cursor)

    def get_board(self, board_id: str, principal: KanbanPrincipal) -> KanbanBoard:
        scope, goal, team = self._scope(board_id, principal, KanbanCapability.READ)
        return self._board(scope, self._store.list_tasks(scope), principal, goal, team)

    def get_snapshot(
        self,
        board_id: str,
        principal: KanbanPrincipal,
    ) -> KanbanSnapshot:
        scope, _, _ = self._scope(
            board_id,
            principal,
            KanbanCapability.READ,
        )
        snapshot = self._store.read_snapshot(scope)
        board = self._board(
            scope,
            list(snapshot.tasks),
            principal,
            snapshot.goal,
            snapshot.team,
        )
        return KanbanSnapshot(
            board=board,
            cards=tuple(self._cards(scope, list(snapshot.tasks))),
            event_sequence=snapshot.event_sequence,
        )

    def create_board(self, command: CreateBoardCommand, principal: KanbanPrincipal) -> KanbanBoard:
        board_id = (
            "hub"
            if command.scope_type == KanbanScopeType.HUB
            else f"{command.scope_type.value}:{command.scope_id}"
        )
        self._scope(board_id, principal, KanbanCapability.WRITE)
        return self.get_board(board_id, principal)

    def list_cards(
        self,
        board_id: str,
        principal: KanbanPrincipal,
        *,
        limit: int = 50,
        cursor: str | None = None,
        column_id: KanbanColumnId | None = None,
        assignee_id: str | None = None,
        blocked: bool | None = None,
        query: str | None = None,
    ) -> KanbanCardPage:
        self._limit(limit)
        scope, _, _ = self._scope(board_id, principal, KanbanCapability.READ)
        tasks = self._store.list_tasks(scope)
        revision = self._revision(scope, tasks)
        cards = self._cards(scope, tasks)
        if column_id:
            cards = [item for item in cards if item.column_id == column_id]
        if assignee_id:
            cards = [item for item in cards if item.assignee and item.assignee.id == assignee_id]
        if blocked is not None:
            cards = [item for item in cards if item.blocked is blocked]
        if query:
            query = query.casefold()
            cards = [
                item
                for item in cards
                if query in item.title.casefold() or query in (item.description or "").casefold()
            ]
        offset = self._offset(cursor, revision)
        page = cards[offset : offset + limit]
        next_cursor = self._cursor(offset + len(page), revision) if offset + len(page) < len(cards) else None
        return KanbanCardPage(
            board_id=board_id,
            board_revision=revision,
            items=tuple(page),
            next_cursor=next_cursor,
        )

    def get_card(self, board_id: str, card_id: str, principal: KanbanPrincipal) -> KanbanCard:
        scope, _, _ = self._scope(board_id, principal, KanbanCapability.READ)
        tasks = self._store.list_tasks(scope)
        card = next((item for item in self._cards(scope, tasks) if item.id == card_id), None)
        if card is None:
            raise KanbanServiceError("kanban_card_not_found", "card was not found", status_code=404)
        return card

    _mutation = staticmethod(kanban_mutation_fingerprint)
    _record = staticmethod(record_kanban_history_event)
    _rank = staticmethod(rank_kanban_tasks)
    _transition = staticmethod(require_kanban_transition)
    _dependencies = staticmethod(require_acyclic_kanban_dependencies)
    _store_error = staticmethod(kanban_store_error)

    def _publish_committed(
        self,
        *,
        action: str,
        task: TaskDB,
        actor_id: str,
        details: dict[str, Any],
        event: KanbanEvent | None,
    ) -> None:
        self._events.publish(
            action=action,
            task=task,
            actor_id=actor_id,
            details=details,
        )
        if event is None or self._event_mirror is None:
            return
        try:
            self._event_mirror.mirror(event)
        except Exception as exc:
            log_audit(
                "kanban.event.mirror.failed",
                {
                    "board_id": event.board_id,
                    "event_id": event.event_id,
                    "error_type": type(exc).__name__,
                },
            )

    def create_card(
        self, board_id: str, command: CreateCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        scope, goal, _ = self._scope(board_id, principal, KanbanCapability.WRITE)
        mutation = self._mutation(
            principal, command.idempotency_key, "create", command.model_dump(mode="json")
        )
        task = TaskDB(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"ananta:{scope.board_id}:{mutation.key_hash}")),
            title=command.title,
            description=command.description,
            status="todo",
            priority=command.priority,
            goal_id=scope.scope_id if scope.kind == "goal" else None,
            team_id=(
                str(goal.team_id)
                if scope.kind == "goal" and getattr(goal, "team_id", None)
                else scope.scope_id if scope.kind == "team" else None
            ),
            depends_on=list(command.dependencies),
        )

        def prepare(created: TaskDB, tasks: list[TaskDB]) -> None:
            all_tasks = [*tasks, created]
            self._dependencies(created, command.dependencies, all_tasks)
            self._rank(
                all_tasks,
                created,
                KanbanColumnId.TODO,
                command.position if command.position is not None else len(tasks),
            )
            self._record(
                created,
                event_type="kanban_card_created",
                message="Card created through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={"board_id": board_id},
            )

        try:
            result = self._store.create_task(
                scope,
                task,
                key_hash=mutation.key_hash,
                request_digest=mutation.digest,
                prepare=prepare,
                event_factory=lambda current, sequence: build_kanban_event(
                    action="kanban.card.created",
                    task=current,
                    details={"board_id": board_id},
                    sequence=sequence,
                ),
            )
        except (KanbanTaskNotFound, KanbanRevisionConflict, KanbanIdempotencyConflict) as exc:
            raise self._store_error(exc) from exc
        if not result.replayed:
            self._publish_committed(
                action="kanban.card.created",
                task=result.task,
                actor_id=principal.subject,
                details={"board_id": board_id},
                event=result.event,
            )
        return self.get_card(board_id, result.task.id, principal)

    def _apply(
        self,
        card_id: str,
        command: Any,
        principal: KanbanPrincipal,
        *,
        name: str,
        capability: KanbanCapability,
        mutate: Any,
        audit: dict[str, Any],
    ) -> KanbanCard:
        scope, _, _ = self._scope(command.board_id, principal, capability)
        mutation = self._mutation(
            principal, command.idempotency_key, name, command.model_dump(mode="json")
        )
        from agent.services.task_mutation_lock_service import (
            get_task_mutation_lock_port,
        )
        from agent.services.task_runtime_service import (
            run_external_task_status_post_commit,
        )

        initial_tasks = self._store.list_tasks(scope)
        initial_task = next(
            (item for item in initial_tasks if item.id == card_id),
            None,
        )
        lock_ids = {str(card_id)}
        source_task_id = str(
            getattr(initial_task, "source_task_id", "") or ""
        ).strip()
        if source_task_id:
            lock_ids.add(source_task_id)
        with get_task_mutation_lock_port().mutation_locks(
            lock_ids
        ) as acquired:
            if not acquired:
                raise KanbanServiceError(
                    "kanban_task_lock_unavailable",
                    "the card mutation fence is unavailable",
                    status_code=409,
                )
            fenced_tasks = self._store.list_tasks(scope)
            fenced_task = next(
                (
                    item
                    for item in fenced_tasks
                    if item.id == card_id
                ),
                None,
            )
            old_status = (
                str(getattr(fenced_task, "status", "") or "")
                if fenced_task is not None
                else None
            )
            require_kanban_mutation_allowed(fenced_task)
            try:
                ensure_external_recovery_mutation_allowed(
                    fenced_task,
                    action=f"kanban_{name}",
                )
            except RecoveryTaskMutationConflict as exc:
                raise KanbanServiceError(
                    exc.reason_code,
                    "Hub Recovery cards can only be changed by the Recovery saga",
                    status_code=409,
                    details=exc.as_data(),
                ) from exc

            def apply_mutation(
                task: TaskDB,
                tasks: list[TaskDB],
            ):
                require_kanban_mutation_allowed(task)
                return mutate(task, tasks, mutation)

            try:
                result = self._store.mutate_task(
                    scope,
                    card_id,
                    expected_revision=command.expected_revision,
                    key_hash=mutation.key_hash,
                    request_digest=mutation.digest,
                    mutate=apply_mutation,
                    event_factory=lambda current, sequence: build_kanban_event(
                        action=f"kanban.card.{name}",
                        task=current,
                        details={
                            "board_id": command.board_id,
                            **audit,
                        },
                        sequence=sequence,
                    ),
                )
            except (
                KanbanTaskNotFound,
                KanbanRevisionConflict,
                KanbanIdempotencyConflict,
            ) as exc:
                raise self._store_error(exc) from exc
        if not result.replayed:
            # The task status itself is already committed and therefore fences
            # new recovery dispatches.  Run cascades only after releasing this
            # card/source lock pair so nested source/child locks cannot invert
            # the global multi-key lock order.
            run_external_task_status_post_commit(
                card_id,
                old_status=old_status,
                event_type=f"kanban_card_{name}",
                force=False,
            )
        if not result.replayed:
            self._publish_committed(
                action=f"kanban.card.{name}",
                task=result.task,
                actor_id=principal.subject,
                details={"board_id": command.board_id, **audit},
                event=result.event,
            )
        return self.get_card(command.board_id, card_id, principal)

    def move_card(
        self, card_id: str, command: MoveCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        def change(task: TaskDB, tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            source = self._column(task.status)
            old_status = str(task.status)
            target_status = COLUMN_TARGET[command.column_id]
            if source != command.column_id:
                self._transition(task, target_status)
                task.status = target_status
            self._rank(tasks, task, command.column_id, command.position, source)
            self._record(
                task,
                event_type="kanban_card_moved",
                message="Card moved through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={
                    "board_id": command.board_id,
                    "from_status": old_status,
                    "to_status": str(task.status),
                    "position": command.position,
                },
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="moved",
            capability=KanbanCapability.WRITE,
            mutate=change,
            audit={"column_id": command.column_id.value, "position": command.position},
        )

    def assign_card(
        self, card_id: str, command: AssignCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        scope, _, _ = self._scope(command.board_id, principal, KanbanCapability.ASSIGN)
        agent = self._store.get_agent(command.assignee_id) if command.assignee_id else None
        if command.assignee_id and agent is None:
            raise KanbanServiceError(
                "kanban_assignee_not_found", "assignee was not found", status_code=404
            )
        if command.assignee_id and scope.kind == "team" and not self._store.is_team_member(
            str(scope.scope_id), command.assignee_id
        ):
            raise KanbanServiceError(
                "kanban_assignee_forbidden",
                "assignee is not a member of this team",
                status_code=409,
            )

        def change(task: TaskDB, _tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            context = dict(task.worker_execution_context or {})
            if agent is None:
                context.pop("kanban_assignee_id", None)
                context.pop("kanban_assignee_name", None)
                task.assigned_agent_url = None
            else:
                context["kanban_assignee_id"] = command.assignee_id
                context["kanban_assignee_name"] = getattr(agent, "name", None)
                task.assigned_agent_url = (
                    getattr(agent, "url", None)
                    or getattr(agent, "agent_url", None)
                    or command.assignee_id
                )
            task.worker_execution_context = context
            self._record(
                task,
                event_type="kanban_card_assigned",
                message="Card assignment changed through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={"board_id": command.board_id, "assignee_id": command.assignee_id},
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="assigned",
            capability=KanbanCapability.ASSIGN,
            mutate=change,
            audit={"assignee_id": command.assignee_id},
        )

    def comment_card(
        self, card_id: str, command: CommentCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        def change(task: TaskDB, _tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            self._record(
                task,
                event_type="kanban_comment_added",
                message="Comment added through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={
                    "board_id": command.board_id,
                    "comment_id": str(uuid.uuid4()),
                    "body": command.body,
                },
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="commented",
            capability=KanbanCapability.COMMENT,
            mutate=change,
            audit={},
        )

    def set_dependencies(
        self, card_id: str, command: SetDependenciesCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        def change(task: TaskDB, tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            self._dependencies(task, command.dependencies, tasks)
            task.depends_on = list(dict.fromkeys(command.dependencies))
            self._record(
                task,
                event_type="kanban_dependencies_changed",
                message="Card dependencies changed through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={"board_id": command.board_id, "dependencies": list(task.depends_on)},
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="dependencies_changed",
            capability=KanbanCapability.WRITE,
            mutate=change,
            audit={"dependency_count": len(command.dependencies)},
        )

    def block_card(
        self, card_id: str, command: BlockCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        def change(task: TaskDB, tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            source = self._column(task.status)
            self._dependencies(task, command.dependencies, tasks)
            self._transition(task, "blocked_by_dependency")
            task.status = "blocked_by_dependency"
            task.depends_on = list(dict.fromkeys(command.dependencies))
            self._rank(tasks, task, KanbanColumnId.BLOCKED, len(tasks), source)
            self._record(
                task,
                event_type="kanban_card_blocked",
                message="Card blocked through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={
                    "board_id": command.board_id,
                    "reason": command.reason,
                    "dependencies": list(task.depends_on),
                },
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="blocked",
            capability=KanbanCapability.WRITE,
            mutate=change,
            audit={"dependency_count": len(command.dependencies)},
        )

    def complete_card(
        self, card_id: str, command: CompleteCardCommand, principal: KanbanPrincipal
    ) -> KanbanCard:
        def change(task: TaskDB, tasks: list[TaskDB], mutation: KanbanMutation) -> None:
            source = self._column(task.status)
            self._transition(task, "completed")
            task.status = "completed"
            self._rank(tasks, task, KanbanColumnId.COMPLETED, len(tasks), source)
            self._record(
                task,
                event_type="kanban_card_completed",
                message="Card completed through Kanban projection",
                actor=principal.subject,
                mutation=mutation,
                details={"board_id": command.board_id, "outcome": command.outcome},
            )

        return self._apply(
            card_id,
            command,
            principal,
            name="completed",
            capability=KanbanCapability.WRITE,
            mutate=change,
            audit={},
        )

    @staticmethod
    def _when(event: dict[str, Any], fallback: datetime) -> datetime:
        value = event.get("timestamp") or event.get("created_at")
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return fallback

    def _task(self, board_id: str, card_id: str, principal: KanbanPrincipal):
        scope, _, _ = self._scope(board_id, principal, KanbanCapability.READ)
        tasks = self._store.list_tasks(scope)
        task = next((item for item in tasks if item.id == card_id), None)
        if task is None:
            raise KanbanServiceError("kanban_card_not_found", "card was not found", status_code=404)
        return task, self._revision(scope, tasks)

    def list_comments(
        self,
        board_id: str,
        card_id: str,
        principal: KanbanPrincipal,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> KanbanCommentPage:
        self._limit(limit)
        task, revision = self._task(board_id, card_id, principal)
        comments = []
        for event in list(task.history or []):
            if not isinstance(event, dict) or self._event_type(event) != "kanban_comment_added":
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            comments.append(
                KanbanComment(
                    id=str(details.get("comment_id") or uuid.uuid5(uuid.NAMESPACE_URL, repr(event))),
                    card_id=card_id,
                    author_id=str(details.get("actor_id") or "unknown"),
                    body=str(details.get("body") or ""),
                    created_at=self._when(event, task.updated_at),
                )
            )
        comments.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        offset = self._offset(cursor, revision)
        page = comments[offset : offset + limit]
        next_cursor = self._cursor(offset + len(page), revision) if offset + len(page) < len(comments) else None
        return KanbanCommentPage(
            board_id=board_id,
            card_id=card_id,
            board_revision=revision,
            items=tuple(page),
            next_cursor=next_cursor,
        )

    def list_activity(
        self,
        board_id: str,
        card_id: str,
        principal: KanbanPrincipal,
        *,
        limit: int = 50,
        cursor: str | None = None,
    ) -> KanbanActivityPage:
        self._limit(limit)
        task, revision = self._task(board_id, card_id, principal)
        activity = []
        for index, event in enumerate(list(task.history or [])):
            if not isinstance(event, dict) or not self._event_type(event).startswith("kanban_"):
                continue
            details = event.get("details") if isinstance(event.get("details"), dict) else {}
            public = {
                key: value
                for key, value in details.items()
                if key not in {"body", "idempotency_key_hash", "idempotency_digest"}
            }
            activity.append(
                KanbanActivity(
                    id=str(
                        details.get("comment_id")
                        or uuid.uuid5(
                            uuid.NAMESPACE_URL, f"{task.id}:{index}:{self._event_type(event)}"
                        )
                    ),
                    card_id=card_id,
                    event_type=self._event_type(event),
                    actor_id=details.get("actor_id"),
                    message=str(
                        event.get("message")
                        or details.get("summary")
                        or self._event_type(event)
                    ),
                    details=public,
                    created_at=self._when(event, task.updated_at),
                )
            )
        activity.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        offset = self._offset(cursor, revision)
        page = activity[offset : offset + limit]
        next_cursor = self._cursor(offset + len(page), revision) if offset + len(page) < len(activity) else None
        return KanbanActivityPage(
            board_id=board_id,
            card_id=card_id,
            board_revision=revision,
            items=tuple(page),
            next_cursor=next_cursor,
        )
