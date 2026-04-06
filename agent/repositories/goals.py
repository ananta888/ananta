from typing import List, Optional

from sqlmodel import Session, select

from agent.database import engine
from agent.db_models import GoalDB, PlanDB, PlanNodeDB


class GoalRepository:
    def get_all(self):
        with Session(engine) as session:
            return session.exec(select(GoalDB).order_by(GoalDB.created_at.desc())).all()

    def get_by_id(self, goal_id: str) -> Optional[GoalDB]:
        with Session(engine) as session:
            return session.get(GoalDB, goal_id)

    def save(self, goal: GoalDB):
        with Session(engine) as session:
            merged = session.merge(goal)
            session.commit()
            session.refresh(merged)
            return merged

    def delete(self, goal_id: str):
        with Session(engine) as session:
            goal = session.get(GoalDB, goal_id)
            if goal:
                session.delete(goal)
                session.commit()
                return True
            return False

    def clear_team_assignments(self, team_id: str) -> int:
        with Session(engine) as session:
            goals = session.exec(select(GoalDB).where(GoalDB.team_id == team_id)).all()
            for goal in goals:
                goal.team_id = None
                session.add(goal)
            session.commit()
            return len(goals)


class PlanRepository:
    def get_by_id(self, plan_id: str) -> Optional[PlanDB]:
        with Session(engine) as session:
            return session.get(PlanDB, plan_id)

    def get_by_goal_id(self, goal_id: str) -> List[PlanDB]:
        with Session(engine) as session:
            statement = select(PlanDB).where(PlanDB.goal_id == goal_id).order_by(PlanDB.created_at.desc())
            return session.exec(statement).all()

    def save(self, plan: PlanDB):
        with Session(engine) as session:
            merged = session.merge(plan)
            session.commit()
            session.refresh(merged)
            return merged


class PlanNodeRepository:
    def get_by_id(self, node_id: str) -> Optional[PlanNodeDB]:
        with Session(engine) as session:
            return session.get(PlanNodeDB, node_id)

    def get_by_plan_id(self, plan_id: str) -> List[PlanNodeDB]:
        with Session(engine) as session:
            statement = select(PlanNodeDB).where(PlanNodeDB.plan_id == plan_id).order_by(PlanNodeDB.position.asc())
            return session.exec(statement).all()

    def save(self, node: PlanNodeDB):
        with Session(engine) as session:
            merged = session.merge(node)
            session.commit()
            session.refresh(merged)
            return merged

    def delete_by_plan_id(self, plan_id: str):
        with Session(engine) as session:
            from sqlmodel import delete

            session.exec(delete(PlanNodeDB).where(PlanNodeDB.plan_id == plan_id))
            session.commit()
