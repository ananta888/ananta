local M = {}

local SUPPORTED_ARTIFACT_TYPES = {
  patch = true,
  diff = true,
  report = true,
  text = true,
  json = true,
  log = true,
}

local function create_scratch_buffer(lines, style)
  local buffer = vim.api.nvim_create_buf(false, true)
  vim.api.nvim_buf_set_lines(buffer, 0, -1, false, lines)
  vim.bo[buffer].bufhidden = "wipe"
  vim.bo[buffer].buftype = "nofile"
  vim.bo[buffer].swapfile = false
  vim.bo[buffer].modifiable = false

  if style == "float" then
    local width = math.min(120, math.max(60, math.floor(vim.o.columns * 0.8)))
    local height = math.min(30, math.max(10, math.floor(vim.o.lines * 0.6)))
    vim.api.nvim_open_win(buffer, true, {
      relative = "editor",
      width = width,
      height = height,
      row = math.floor((vim.o.lines - height) / 2),
      col = math.floor((vim.o.columns - width) / 2),
      style = "minimal",
      border = "rounded",
    })
  else
    vim.cmd("botright new")
    vim.api.nvim_win_set_buf(0, buffer)
  end
end

local function line_for_artifacts(data, lines)
  local artifacts = data and data.artifacts
  if type(artifacts) ~= "table" then
    return
  end
  for _, artifact in ipairs(artifacts) do
    local artifact_type = tostring(artifact.type or "unknown")
    local artifact_id = tostring(artifact.id or "unknown")
    if not SUPPORTED_ARTIFACT_TYPES[artifact_type] then
      table.insert(lines, "artifact=" .. artifact_id .. " unsupported_type=" .. artifact_type .. " fallback=browser_or_text")
    else
      table.insert(lines, "artifact=" .. artifact_id .. " type=" .. artifact_type)
    end
  end
end

function M.response_lines(command_name, response)
  local lines = {
    "[ANANTA NVIM RESULT]",
    "command=" .. tostring(command_name),
    "ok=" .. tostring(response.ok == true),
    "state=" .. tostring(response.state or "unknown_error"),
    "status=" .. tostring(response.status_code or "none"),
  }

  if response.error and response.error ~= "" then
    table.insert(lines, "error=" .. tostring(response.error))
  end
  if type(response.data) == "table" then
    if response.data.task_id then
      table.insert(lines, "task_id=" .. tostring(response.data.task_id))
    end
    if response.data.goal_id then
      table.insert(lines, "goal_id=" .. tostring(response.data.goal_id))
    end
    if response.data.browser_url then
      table.insert(lines, "browser_url=" .. tostring(response.data.browser_url))
    end
    line_for_artifacts(response.data, lines)
  end
  return lines
end

function M.show_response(command_name, response, opts)
  local style = (opts and opts.style) or "split"
  local lines = M.response_lines(command_name, response)
  if #vim.api.nvim_list_uis() == 0 then
    print(table.concat(lines, "\n"))
    return lines
  end
  create_scratch_buffer(lines, style)
  return lines
end

function M.show_context_preview(payload)
  local lines = {
    "[ANANTA CONTEXT PREVIEW]",
    "file_path=" .. tostring(payload.file_path or "none"),
    "project_root=" .. tostring(payload.project_root or "none"),
    "selection_clipped=" .. tostring(payload.selection_clipped),
    "bounded=" .. tostring(payload.bounded),
    "implicit_unrelated_paths_included=" .. tostring(payload.implicit_unrelated_paths_included),
  }
  if payload.selection_text and payload.selection_text ~= "" then
    table.insert(lines, "selection_preview=" .. tostring(payload.selection_text):gsub("\n", " "):sub(1, 140))
  else
    table.insert(lines, "selection_preview=none")
  end

  if #vim.api.nvim_list_uis() == 0 then
    print(table.concat(lines, "\n"))
    return lines
  end
  create_scratch_buffer(lines, "float")
  return lines
end

return M
