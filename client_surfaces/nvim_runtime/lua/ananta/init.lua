local context = require("ananta.context")
local client = require("ananta.client")
local render = require("ananta.render")

local M = {}

local config = {
  profile_id = "nvim-default",
  base_url = "http://localhost:8080",
  auth_mode = "session_token",
  auth_token = "",
  environment = "local",
  timeout_seconds = 8.0,
  max_selection_chars = 2000,
  confirm_context = false,
  render_style = "split",
}

local function merge_opts(base, override)
  local merged = {}
  for key, value in pairs(base) do
    merged[key] = value
  end
  for key, value in pairs(override or {}) do
    merged[key] = value
  end
  return merged
end

local function maybe_confirm_context(context_payload)
  if config.confirm_context ~= true then
    return true
  end
  render.show_context_preview(context_payload)
  local choice = vim.fn.confirm("Send bounded context to Ananta?", "&Send\n&Cancel", 1)
  return choice == 1
end

local function execute(command_name, opts)
  local options = opts or {}
  local context_payload = context.capture_current({
    max_selection_chars = config.max_selection_chars,
    use_visual_selection = options.use_visual_selection == true,
  })
  render.show_context_preview(context_payload)
  if not maybe_confirm_context(context_payload) then
    local cancelled = {
      ok = false,
      status_code = nil,
      state = "policy_denied",
      data = nil,
      error = "user_cancelled_context_confirmation",
      retriable = false,
    }
    render.show_response(command_name, cancelled, { style = config.render_style })
    return cancelled
  end
  local response = client.execute_command(command_name, {
    goal_text = options.goal_text,
    context_payload = context_payload,
    config = config,
  })
  render.show_response(command_name, response, { style = config.render_style })
  return response
end

function M.setup(opts)
  config = merge_opts(config, opts or {})
end

function M.goal_submit(goal_text)
  local text = tostring(goal_text or "")
  if text == "" then
    text = "Goal from Neovim"
  end
  return execute("goal_submit", { goal_text = text })
end

function M.analyze()
  return execute("analyze", { use_visual_selection = false })
end

function M.review()
  return execute("review", { use_visual_selection = true })
end

function M.patch_plan()
  return execute("patch_plan", { use_visual_selection = true })
end

function M.project_new(goal_text)
  local text = tostring(goal_text or "")
  if text == "" then
    text = "New project request from Neovim"
  end
  return execute("project_new", { goal_text = text })
end

function M.project_evolve(goal_text)
  local text = tostring(goal_text or "")
  if text == "" then
    text = "Project evolution request from Neovim"
  end
  return execute("project_evolve", { goal_text = text })
end

function M.inspect_context()
  local context_payload = context.capture_current({
    max_selection_chars = config.max_selection_chars,
    use_visual_selection = true,
  })
  render.show_context_preview(context_payload)
  return context_payload
end

return M
