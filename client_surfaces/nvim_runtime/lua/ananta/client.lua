local M = {}

local function map_status_to_state(status_code, parse_error)
  if parse_error then
    return "malformed_response"
  end
  if status_code == nil then
    return "backend_unreachable"
  end
  if status_code >= 200 and status_code < 300 then
    return "healthy"
  end
  if status_code == 401 then
    return "auth_failed"
  end
  if status_code == 403 then
    return "policy_denied"
  end
  if status_code == 422 then
    return "capability_missing"
  end
  if status_code >= 500 then
    return "backend_unreachable"
  end
  return "unknown_error"
end

local function retriable_for_state(state)
  return state == "backend_unreachable" or state == "malformed_response" or state == "unknown_error"
end

local function fixture_response(command)
  local data = {
    goal_submit = { goal_id = "goal-nvim-fixture", task_id = "task-nvim-goal-1", browser_url = "http://localhost:8080/goals/goal-nvim-fixture" },
    analyze = { task_id = "task-nvim-analyze-1", status = "queued", summary = "Analyze accepted" },
    review = { task_id = "task-nvim-review-1", status = "queued", summary = "Review accepted" },
    patch_plan = { task_id = "task-nvim-patch-1", status = "queued", summary = "Patch planning accepted" },
    project_new = { task_id = "task-nvim-project-new-1", status = "queued", summary = "Project creation accepted" },
    project_evolve = { task_id = "task-nvim-project-evolve-1", status = "queued", summary = "Project evolution accepted" },
  }
  return {
    ok = true,
    status_code = 200,
    state = "healthy",
    data = data[command] or { status = "ok" },
    error = nil,
    retriable = false,
  }
end

local function parse_bridge_output(raw)
  local ok, decoded = pcall(vim.json.decode, raw)
  if not ok or type(decoded) ~= "table" or type(decoded.response) ~= "table" then
    return {
      ok = false,
      status_code = nil,
      state = "malformed_response",
      data = nil,
      error = "bridge_output_malformed",
      retriable = true,
    }
  end
  local response = decoded.response
  return {
    ok = response.ok == true,
    status_code = response.status_code,
    state = response.state or map_status_to_state(response.status_code, false),
    data = response.data,
    error = response.error,
    retriable = response.retriable == true,
  }
end

local function build_bridge_args(command, goal_text, context_payload, config)
  local python = vim.fn.exepath("python3")
  if python == "" then
    python = vim.fn.exepath("python")
  end
  if python == "" then
    return nil
  end
  return {
    python,
    "-m",
    "client_surfaces.nvim_runtime.ananta_bridge",
    "--command",
    command,
    "--goal-text",
    tostring(goal_text or ""),
    "--base-url",
    tostring(config.base_url or "http://localhost:8080"),
    "--profile-id",
    tostring(config.profile_id or "nvim-default"),
    "--auth-mode",
    tostring(config.auth_mode or "session_token"),
    "--auth-token",
    tostring(config.auth_token or ""),
    "--environment",
    tostring(config.environment or "local"),
    "--timeout-seconds",
    tostring(config.timeout_seconds or 8.0),
    "--file-path",
    tostring(context_payload.file_path or ""),
    "--project-root",
    tostring(context_payload.project_root or ""),
    "--selection-text",
    tostring(context_payload.selection_text or ""),
    "--max-selection-chars",
    tostring(config.max_selection_chars or 2000),
  }
end

function M.execute_command(command, opts)
  if vim.env.ANANTA_NVIM_FIXTURE == "1" then
    return fixture_response(command)
  end

  local options = opts or {}
  local context_payload = options.context_payload or {}
  local args = build_bridge_args(command, options.goal_text, context_payload, options.config or {})
  if not args then
    return {
      ok = false,
      status_code = nil,
      state = "backend_unreachable",
      data = nil,
      error = "python_runtime_missing",
      retriable = false,
    }
  end

  local raw = vim.fn.system(args)
  local shell_error = vim.v.shell_error
  local parsed = parse_bridge_output(raw)
  if shell_error ~= 0 and parsed.ok then
    local state = map_status_to_state(parsed.status_code, false)
    return {
      ok = false,
      status_code = parsed.status_code,
      state = state,
      data = parsed.data,
      error = parsed.error or "bridge_command_failed",
      retriable = retriable_for_state(state),
    }
  end
  return parsed
end

return M
