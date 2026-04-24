local M = {}

local function clip_text(value, max_chars)
  local text = tostring(value or "")
  local limit = math.max(1, tonumber(max_chars) or 1)
  if #text > limit then
    return text:sub(1, limit), true
  end
  return text, false
end

local function current_file_path()
  local path = vim.api.nvim_buf_get_name(0)
  if path == "" then
    return nil
  end
  return path
end

local function capture_visual_selection()
  local mode = vim.fn.mode()
  if mode ~= "v" and mode ~= "V" and mode ~= "\22" then
    return nil
  end
  local start_pos = vim.fn.getpos("'<")
  local end_pos = vim.fn.getpos("'>")
  local start_row = math.max(1, tonumber(start_pos[2]) or 1)
  local start_col = math.max(1, tonumber(start_pos[3]) or 1)
  local end_row = math.max(start_row, tonumber(end_pos[2]) or start_row)
  local end_col = math.max(start_col, tonumber(end_pos[3]) or start_col)
  local lines = vim.api.nvim_buf_get_lines(0, start_row - 1, end_row, false)
  if #lines == 0 then
    return nil
  end
  lines[1] = string.sub(lines[1], start_col)
  lines[#lines] = string.sub(lines[#lines], 1, end_col)
  return table.concat(lines, "\n")
end

local function capture_buffer_excerpt(max_chars)
  local lines = vim.api.nvim_buf_get_lines(0, 0, -1, false)
  local text = table.concat(lines, "\n")
  local excerpt, clipped = clip_text(text, max_chars)
  return excerpt, clipped
end

local function has_secret_pattern(text)
  local lower = string.lower(text or "")
  return lower:find("token", 1, true)
      or lower:find("secret", 1, true)
      or lower:find("password", 1, true)
      or lower:find("api_key", 1, true)
end

function M.capture_current(opts)
  local options = opts or {}
  local max_selection_chars = tonumber(options.max_selection_chars) or 2000
  local file_path = current_file_path()
  local project_root = vim.fn.getcwd()
  local selection = nil
  local clipped = false

  if options.use_visual_selection then
    selection = capture_visual_selection()
  end
  if not selection or selection == "" then
    selection, clipped = capture_buffer_excerpt(max_selection_chars)
  else
    selection, clipped = clip_text(selection, max_selection_chars)
  end

  local warnings = {}
  if has_secret_pattern(selection) then
    table.insert(warnings, "selection_may_contain_secret")
  end

  return {
    schema = "client_bounded_context_payload_v1",
    file_path = file_path,
    project_root = project_root,
    selection_text = selection ~= "" and selection or nil,
    selection_clipped = clipped,
    extra_paths = {},
    rejected_paths = {},
    provenance = {
      has_selection = selection ~= "",
      has_file_path = file_path ~= nil,
      has_project_root = project_root ~= "",
      extra_paths_count = 0,
    },
    warnings = warnings,
    bounded = true,
    implicit_unrelated_paths_included = false,
  }
end

function M.preview_lines(payload)
  local lines = {
    "[ANANTA CONTEXT PREVIEW]",
    "schema=" .. tostring(payload.schema),
    "file_path=" .. tostring(payload.file_path or "none"),
    "project_root=" .. tostring(payload.project_root or "none"),
    "selection_clipped=" .. tostring(payload.selection_clipped),
    "extra_paths_count=" .. tostring(payload.provenance.extra_paths_count),
  }
  if payload.selection_text and payload.selection_text ~= "" then
    table.insert(lines, "selection_preview=" .. payload.selection_text:gsub("\n", " "):sub(1, 140))
  else
    table.insert(lines, "selection_preview=none")
  end
  if payload.warnings and #payload.warnings > 0 then
    table.insert(lines, "warnings=" .. table.concat(payload.warnings, ","))
  end
  return lines
end

return M
