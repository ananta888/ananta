bash_integration = """
# Shell-GPT integration BASH v0.2
_sgpt_bash() {
if [[ -n "$READLINE_LINE" ]]; then
    READLINE_LINE=$(sgpt --shell <<< "$READLINE_LINE" --no-interaction)
    READLINE_POINT=${#READLINE_LINE}
fi
}
bind -x '"\\C-l": _sgpt_bash'
# Shell-GPT integration BASH v0.2
"""

zsh_integration = """
# Shell-GPT integration ZSH v0.2
_sgpt_zsh() {
if [[ -n "$BUFFER" ]]; then
    _sgpt_prev_cmd=$BUFFER
    BUFFER+="⌛"
    zle -I && zle redisplay
    BUFFER=$(sgpt --shell <<< "$_sgpt_prev_cmd" --no-interaction)
    zle end-of-line
fi
}
zle -N _sgpt_zsh
bindkey ^l _sgpt_zsh
# Shell-GPT integration ZSH v0.2
"""

pwsh_integration = """
# Shell-GPT integration PowerShell v0.1
function Invoke-Sgpt {
    $currentLine = $Context.Cursor.Line
    if (-not [string]::IsNullOrWhiteSpace($currentLine)) {
        $result = $currentLine | sgpt --shell --no-interaction
        [Microsoft.PowerShell.PSConsoleReadLine]::DeleteLine()
        [Microsoft.PowerShell.PSConsoleReadLine]::Insert($result)
    }
}
Set-PSReadLineKeyHandler -Key "Ctrl+l" -Function Invoke-Sgpt
# Shell-GPT integration PowerShell v0.1
"""
