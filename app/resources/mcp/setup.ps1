#Requires -Version 5.1
<#
.SYNOPSIS
  MCP setup for the HPC release system (served by GET /api/mcp/setup.ps1).

.DESCRIPTION
  用法示例：
    irm __BASE_URL__/api/mcp/setup.ps1 | iex                                          # 菜单
    & ([scriptblock]::Create((irm __BASE_URL__/api/mcp/setup.ps1))) configure         # 或 status / regenerate / uninstall

    $env:MCP_CLIENTS='claude,cursor'; & ([scriptblock]::Create((irm __BASE_URL__/api/mcp/setup.ps1))) use-token
    # 可选 MCP_CLIENTS：claude, codex, cursor, opencode, codebuddy, dsh (别名 deepseek-harness), all
    # use-token 会提示输入已有 Token，或直接读取 $env:MCP_TOKEN。

  也可以通过环境变量传入动作：
    $env:MCP_ACTION='status'; irm __BASE_URL__/api/mcp/setup.ps1 | iex

  Config files are edited directly (no client CLI needed), so machines
  with only VS Code extensions work too:
    Claude Code       ~/.claude.json                                    mcpServers.release-system
    Codex             ~/.codex/config.toml                              [mcp_servers.release-system]
    Cursor            ~/.cursor/mcp.json                                mcpServers.release-system
    OpenCode          ~/.config/opencode/opencode.jsonc (or existing)   mcp.release-system
    CodeBuddy         ~/.codebuddy/.mcp.json + VS Code globalStorage    mcpServers.release-system
    DeepSeek Harness  ~/.dsh/cordis.patch.yml                           @deepseek-ai/dsh-mcp-client
#>
param(
    [Parameter(Position = 0)]
    [string]$Action = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# 兼容 Windows PowerShell 5.1 的 TLS 1.2
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {}

$BASE_URL      = "__BASE_URL__"
$MCP_URL       = "$BASE_URL/mcp"
$SERVER_NAME   = "release-system"
$ALL_CLIENTS   = @("claude", "codex", "cursor", "opencode", "codebuddy", "dsh")

$CLAUDE_JSON   = Join-Path $HOME ".claude.json"
$CODEX_HOME    = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }
$CODEX_TOML    = Join-Path $CODEX_HOME "config.toml"
$CURSOR_JSON   = Join-Path $HOME ".cursor\mcp.json"
$OPENCODE_DIR  = if ($env:XDG_CONFIG_HOME) {
    Join-Path $env:XDG_CONFIG_HOME "opencode"
} else {
    Join-Path $HOME ".config\opencode"
}
$OPENCODE_JSON = if ($env:OPENCODE_CONFIG) {
    $env:OPENCODE_CONFIG
} else {
    Join-Path $OPENCODE_DIR "opencode.jsonc"
}
$DSH_HOME_DIR  = if ($env:DSH_HOME) { $env:DSH_HOME } else { Join-Path $HOME ".dsh" }
$DSH_PATCH_YML = Join-Path $DSH_HOME_DIR "cordis.patch.yml"

$hostShort = try { [System.Net.Dns]::GetHostName() } catch { $env:COMPUTERNAME }
$TOKEN_NAME = if ($env:MCP_TOKEN_NAME) { $env:MCP_TOKEN_NAME } else { "$hostShort 一键配置" }

$CODEX_SECTION_RE = '^\s*\[\s*mcp_servers\.' + [regex]::Escape($SERVER_NAME) + '(\.[^\]]*)?\s*\]\s*$'
$CODEX_HEADER_RE  = '^\s*\['
$TOKEN_RE         = 'Bearer\s+(rsmcp_[A-Za-z0-9_\-]+)'
$DSH_BEGIN        = "# BEGIN $SERVER_NAME-mcp (managed by hpc-release-system)"
$DSH_END          = "# END $SERVER_NAME-mcp"

# ── 输出与交互 ────────────────────────────────────────────────────────
$script:UseColor  = -not (Test-Path Env:NO_COLOR)
$script:ABORT_TAG = "__HPC_MCP_ABORT__"

function Write-Colored {
    param(
        [string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::Gray,
        [switch]$NoNewline
    )
    if ($script:UseColor) {
        Write-Host $Message -ForegroundColor $Color -NoNewline:$NoNewline
    } else {
        Write-Host $Message -NoNewline:$NoNewline
    }
}

function Say     { param([string]$Msg) Write-Colored $Msg Cyan }
function Dim     { param([string]$Msg) Write-Colored $Msg DarkGray }
function Success { param([string]$Msg) Write-Colored $Msg Green }
function Warn    { param([string]$Msg) Write-Colored $Msg Yellow }
function Bold    { param([string]$Msg) Write-Colored $Msg White }

function Die {
    param([string]$Msg)
    Write-Colored "✘ 错误：$Msg" Red
    throw $script:ABORT_TAG
}

function Ask {
    param([string]$Prompt)
    Write-Colored $Prompt White -NoNewline
    $reply = Read-Host
    if ($null -eq $reply) { return "" }
    return $reply
}

function Ask-Secret {
    param([string]$Prompt)
    Write-Colored $Prompt White -NoNewline
    $sec = Read-Host -AsSecureString
    if ($null -eq $sec) { return "" }
    $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
    try {
        return [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
}

function Show-ReuseNotice {
    param([string]$Token = "")
    Write-Host ""
    Warn '━━━━━━━━━━━━━━━━━━━━━━ ⚠️  重要提示 ━━━━━━━━━━━━━━━━━━━━━━'
    if (-not [string]::IsNullOrEmpty($Token)) {
        Bold '🔑 完整 MCP Token（请妥善保存，服务端仅保存摘要）：'
        Success "   $Token"
        Write-Host ""
    }
    Warn '┃ 1. 每个账号仅保留【一个】有效 Token，支持多台机器共用。'
    Warn '┃ 2. 配置其他机器时，直接粘贴此 Token 即可复用。'
    Warn '┃ 3. 若选择 [重新生成]，其他机器上的旧 Token 将立即失效！'
    Warn '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━'
}

function Show-Token {
    param([string]$Token)
    Show-ReuseNotice $Token
}

function Confirm-YesNo {
    param(
        [string]$Question,
        [ValidateSet("y", "n")]
        [string]$Default = "n"
    )
    $reply = Ask $Question
    if ([string]::IsNullOrWhiteSpace($reply)) { $reply = $Default }
    return ($reply -match '^[Yy]')
}

# ── 服务端 API（用户名 + 密码） ───────────────────────────────────────
$script:Username     = ""
$script:Password     = ""
$script:ActivePrefix = ""
$script:ActiveLine   = ""

function Get-CurrentSystemUser {
    if ($env:USERNAME) { return $env:USERNAME }
    if ($env:USER)     { return $env:USER }
    return ""
}

function Read-Credentials {
    if (-not [string]::IsNullOrEmpty($script:Username)) { return }
    $defaultUser = if ($env:MCP_USER) { $env:MCP_USER } else { Get-CurrentSystemUser }
    Say "请输入 HPC 发布系统账号进行验证："
    $u = Ask "  网站 / 域账号用户名 [$defaultUser]: "
    $script:Username = if ([string]::IsNullOrEmpty($u)) { $defaultUser } else { $u }
    $script:Password = Ask-Secret "  密码（输入时不显示）: "
    if ([string]::IsNullOrEmpty($script:Username) -or [string]::IsNullOrEmpty($script:Password)) {
        Die "用户名和密码不能为空"
    }
}

function Invoke-Api {
    param([string]$ApiAction)
    if ([string]::IsNullOrEmpty($script:Username)) {
        Die "内部错误：未输入用户名"
    }

    $pairs = @(
        "action=$([System.Uri]::EscapeDataString($ApiAction))",
        "username=$([System.Uri]::EscapeDataString($script:Username))",
        "name=$([System.Uri]::EscapeDataString($TOKEN_NAME))",
        "password=$([System.Uri]::EscapeDataString($script:Password))"
    )
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes(($pairs -join "&"))

    try {
        $resp = Invoke-WebRequest -UseBasicParsing `
            -Uri "$BASE_URL/api/mcp/account" `
            -Method Post `
            -Headers @{ Accept = "text/plain" } `
            -ContentType "application/x-www-form-urlencoded; charset=utf-8" `
            -Body $bodyBytes
    } catch {
        $errResp = $null
        if ($_.Exception -and $null -ne $_.Exception.PSObject.Properties['Response']) {
            $errResp = $_.Exception.Response
        }
        if ($null -eq $errResp) {
            Die "无法连接服务器：$BASE_URL"
        }
        $statusCode = [int]$errResp.StatusCode
        $errMsg = ""
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) {
            $errMsg = $_.ErrorDetails.Message
        } else {
            try {
                $stream = $errResp.GetResponseStream()
                if ($null -ne $stream) {
                    $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                    $errMsg = $reader.ReadToEnd()
                    $reader.Close()
                }
            } catch {}
        }
        Die "请求失败（HTTP $statusCode）：$($errMsg.Trim())"
    }

    if ($resp.RawContentStream -and $resp.RawContentStream.CanSeek) {
        $resp.RawContentStream.Position = 0
        $reader = New-Object System.IO.StreamReader($resp.RawContentStream, [System.Text.Encoding]::UTF8)
        $text = $reader.ReadToEnd()
        $reader.Close()
        return $text
    }
    return [string]$resp.Content
}

function Update-ServerStatus {
    Read-Credentials
    $body = Invoke-Api "status"
    $firstLine = ($body -split "\r?\n")[0]
    $script:ActivePrefix = ""
    $script:ActiveLine   = ""

    $parts = $firstLine -split "`t"
    if ($parts.Length -ge 2 -and $parts[0] -eq "active") {
        $script:ActivePrefix = $parts[1]
        $tName      = if ($parts.Length -gt 2) { $parts[2] } else { "" }
        $created    = if ($parts.Length -gt 3) { $parts[3] } else { "" }
        $lastUsed   = if ($parts.Length -gt 4) { $parts[4] } else { "" }
        $lastClient = if ($parts.Length -gt 5) { $parts[5] } else { "" }
        $script:ActiveLine = "$($script:ActivePrefix)…（名称：$tName｜创建于：$created｜最近使用：$lastUsed｜客户端：$lastClient）"
    }
}

function Test-TokenWorks {
    param([string]$Token)
    try {
        $resp = Invoke-WebRequest -UseBasicParsing `
            -Uri $MCP_URL `
            -Method Post `
            -Headers @{ Authorization = "Bearer $Token" } `
            -ContentType "application/json" `
            -Body '{"jsonrpc":"2.0","id":1,"method":"ping"}'
        return ([int]$resp.StatusCode -eq 200)
    } catch {
        return $false
    }
}

# ── 本机配置文件读写（纯 PowerShell，无需外部依赖） ─────────────────────
function Extract-BearerToken {
    param($Auth)
    if ($Auth -is [string] -and $Auth.StartsWith("Bearer ", [System.StringComparison]::Ordinal)) {
        return $Auth.Substring(7)
    }
    return ""
}

function Save-TextAtomic {
    param([string]$Path, [string]$Text, [string]$Client)
    $folder = Split-Path -Path $Path -Parent
    if ([string]::IsNullOrEmpty($folder)) { $folder = "." }
    if (-not (Test-Path -LiteralPath $folder)) {
        New-Item -ItemType Directory -Path $folder -Force | Out-Null
    }
    if (Test-Path -LiteralPath $Path) {
        Copy-Item -LiteralPath $Path -Destination "$Path.bak-release-mcp" -Force
    }
    $tmp = Join-Path $folder (".release-mcp-" + [System.Guid]::NewGuid().ToString("N"))
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($tmp, $Text, $utf8NoBom)
        $isUnix = (Get-Variable -Name IsLinux -ValueOnly -ErrorAction SilentlyContinue) -or `
                  (Get-Variable -Name IsMacOS -ValueOnly -ErrorAction SilentlyContinue)
        if ($isUnix) {
            & chmod 600 $tmp 2>$null
        }
        Move-Item -LiteralPath $tmp -Destination $Path -Force
    } catch {
        if (Test-Path -LiteralPath $tmp) {
            Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
        }
        throw
    }
    if ($Client) {
        [PSCustomObject]@{ Client = $Client; Path = $Path }
    }
}

function Get-JsoncTokens {
    param([string]$Text)
    $pattern = '"(?:[^"\\\x00-\x1F]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"|//[^\r\n]*|/\*[\s\S]*?\*/|[{}\[\]:,]|[^\s{}\[\]:,/"\uFEFF]+|\S'
    return [regex]::Matches($Text, $pattern) | Where-Object {
        -not $_.Value.StartsWith('//') -and -not $_.Value.StartsWith('/*') -and
        $_.Value -ne [string][char]0xFEFF
    }
}

function ConvertFrom-JsonString {
    param([string]$Token)
    return (ConvertFrom-Json -InputObject ('{"value":' + $Token + '}')).value
}

function Read-JsoncDocument {
    param([string]$Path)
    $text = if (Test-Path -LiteralPath $Path) {
        [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    } else { "{}`n" }
    $tokens = @(Get-JsoncTokens $text)
    if ($tokens.Count -eq 0) {
        if ($text -and -not ($text.EndsWith("`n") -or $text.EndsWith("`r"))) { $text += "`n" }
        $text += "{}`n"
        $tokens = @(Get-JsoncTokens $text)
    }
    if ($tokens[0].Value -cne '{') { throw 'JSON root must be an object' }
    $parts = for ($index = 0; $index -lt $tokens.Count; $index++) {
        $part = $tokens[$index].Value
        if ($part -ceq '"') { throw 'Invalid JSON string' }
        if ($part.StartsWith('"')) {
            # Unique placeholders let Windows PowerShell validate the structure
            # without treating case-distinct property names as duplicates.
            '"string_' + $index + '"'
            continue
        }
        if ($part -cnotmatch '^(?:[{}\[\]:,]|true|false|null|-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)$') {
            throw 'Invalid JSON token'
        }
        if ($part -eq ',' -and $index -gt 0 -and $index + 1 -lt $tokens.Count -and
            $tokens[$index - 1].Value -notin @('{', '[') -and $tokens[$index + 1].Value -in @('}', ']')) {
            continue
        }
        $part
    }
    $null = ConvertFrom-Json -InputObject ($parts -join ' ')
    return [PSCustomObject]@{ Text = $text; Tokens = $tokens }
}

function Get-JsoncProperties {
    param(
        [object[]]$Tokens,
        [int]$Opening,
        [System.StringComparer]$Comparer = [System.StringComparer]::Ordinal
    )
    $properties = New-Object 'System.Collections.Generic.Dictionary[string, object]' $Comparer
    $key = $Opening + 1
    while ($Tokens[$key].Value -ne '}') {
        $first = $key + 2
        $last = $first
        if ($Tokens[$first].Value -in @('{', '[')) {
            $depth = 1
            while ($depth -gt 0) {
                $last++
                $part = $Tokens[$last].Value
                if ($part -in @('{', '[')) { $depth++ }
                if ($part -in @('}', ']')) { $depth-- }
            }
        }
        $name = ConvertFrom-JsonString $Tokens[$key].Value
        $properties[$name] = @($key, $first, $last)
        $key = $last + 1
        if ($Tokens[$key].Value -eq ',') { $key++ }
    }
    return ,$properties
}

function Format-JsoncValue {
    param($Value, [int]$Level, [string]$Newline)
    return ((ConvertTo-Json -InputObject $Value -Depth 100) -replace '\r?\n', ($Newline + ('  ' * $Level)))
}

function Add-JsoncProperties {
    param([string]$Text, [int]$Opening, [bool]$HasProperties, $Additions, [int]$Level, [string]$Newline)
    $rendered = Format-JsoncValue $Additions $Level $Newline
    $insertion = $rendered.Substring(1, $rendered.LastIndexOf($Newline) - 1)
    if ($HasProperties) { $insertion += ',' }
    $tail = $Text.Substring($Opening)
    if (-not ($tail.StartsWith("`r") -or $tail.StartsWith("`n"))) {
        $insertion += $Newline + ('  ' * ($Level + [int]$HasProperties))
    }
    return $Text.Insert($Opening, $insertion)
}

function Edit-McpJsonc {
    param([string]$Path, [string]$Section, [string]$Client, $Entry)
    $writing = $null -ne $Entry
    if (-not $writing -and -not (Test-Path -LiteralPath $Path)) { return }
    $document = Read-JsoncDocument $Path
    $text = $document.Text
    $tokens = $document.Tokens
    $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $root = Get-JsoncProperties $tokens 0
    if (-not $root.ContainsKey($Section)) {
        if (-not $writing) { return }
        $additions = [ordered]@{}
        if ($root.Count -eq 0 -and $Client -eq 'opencode') {
            $additions['$schema'] = 'https://opencode.ai/config.json'
        }
        $additions[$Section] = @{ $SERVER_NAME = $Entry }
        $updated = Add-JsoncProperties $text ($tokens[0].Index + 1) ($root.Count -gt 0) $additions 0 $newline
    } else {
        $opening = $root[$Section][1]
        if ($tokens[$opening].Value -ne '{') {
            if (-not $writing) { return }
            if ($tokens[$opening].Value -cne 'null') { throw "$Section must be a JSON object" }
            $value = Format-JsoncValue @{ $SERVER_NAME = $Entry } 1 $newline
            $position = $tokens[$opening].Index
            $updated = $text.Remove($position, $tokens[$opening].Length).Insert($position, $value)
            Save-TextAtomic $Path $updated -Client $Client
            return
        }
        $servers = Get-JsoncProperties $tokens $opening
        if (-not $servers.ContainsKey($SERVER_NAME)) {
            if (-not $writing) { return }
            $updated = Add-JsoncProperties $text ($tokens[$opening].Index + 1) ($servers.Count -gt 0) @{ $SERVER_NAME = $Entry } 1 $newline
        } else {
            $key, $first, $last = $servers[$SERVER_NAME]
            $end = $tokens[$last].Index + $tokens[$last].Length
            if ($writing) {
                $start = $tokens[$first].Index
                $updated = $text.Remove($start, $end - $start).Insert($start, (Format-JsoncValue $Entry 2 $newline))
            } else {
                # Remove one separator, leaving surrounding whitespace and comments in place.
                $start = $tokens[$key].Index
                if ($tokens[$last + 1].Value -eq ',') {
                    $text = $text.Remove($tokens[$last + 1].Index, 1)
                }
                $updated = $text.Remove($start, $end - $start)
                if ($tokens[$last + 1].Value -ne ',' -and $tokens[$key - 1].Value -eq ',') {
                    $updated = $updated.Remove($tokens[$key - 1].Index, 1)
                }
            }
        }
    }
    if ($writing) { Save-TextAtomic $Path $updated -Client $Client }
    else { Save-TextAtomic $Path $updated }
}

function Remove-CodexSection {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $lines = [regex]::Split($Text, '(?<=\n)')
    $kept = New-Object System.Collections.Generic.List[string]
    $skipping = $false
    foreach ($line in $lines) {
        if ($line -eq "") { continue }
        $clean = $line.TrimEnd("`r", "`n")
        if ($clean -match $CODEX_HEADER_RE) {
            $skipping = ($clean -match $CODEX_SECTION_RE)
        }
        if (-not $skipping) {
            $kept.Add($line)
        }
    }
    return (($kept -join "") -replace '(\r?\n)+$', '')
}

function Get-CodexToken {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $inside = $false
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match $CODEX_HEADER_RE) {
            $inside = ($line -match $CODEX_SECTION_RE)
        } elseif ($inside) {
            if ($line -match $TOKEN_RE) {
                return $Matches[1]
            }
        }
    }
    return ""
}

function Get-CodeBuddyCliCandidates {
    return @(
        (Join-Path $HOME ".codebuddy\.mcp.json"),
        (Join-Path $HOME ".codebuddy\mcp.json"),
        (Join-Path $HOME ".codebuddy.json")
    )
}

function Get-FirstExistingConfig {
    param([string[]]$Paths)
    foreach ($p in $Paths) {
        if (Test-Path -LiteralPath $p) { return $p }
    }
    return $Paths[0]
}

function Get-OpenCodeGlobalConfigPaths {
    return @(
        (Join-Path $OPENCODE_DIR 'opencode.jsonc')
        (Join-Path $OPENCODE_DIR 'opencode.json')
        (Join-Path $OPENCODE_DIR 'config.json')
    )
}

function Get-OpenCodeConfigPath {
    if ($env:OPENCODE_CONFIG) { return $OPENCODE_JSON }
    return Get-FirstExistingConfig (Get-OpenCodeGlobalConfigPaths)
}

function Get-CodeBuddyExtPaths {
    $roots = New-Object System.Collections.Generic.List[string]
    if ($env:APPDATA) {
        $roots.Add((Join-Path $env:APPDATA "Code\User\globalStorage"))
        $roots.Add((Join-Path $env:APPDATA "CodeBuddy\User\globalStorage"))
        $roots.Add((Join-Path $env:APPDATA "Cursor\User\globalStorage"))
    }
    $roots.Add((Join-Path $HOME ".config\Code\User\globalStorage"))
    $roots.Add((Join-Path $HOME ".config\CodeBuddy\User\globalStorage"))
    $roots.Add((Join-Path $HOME ".config\Cursor\User\globalStorage"))
    $roots.Add((Join-Path $HOME ".vscode-server\data\User\globalStorage"))
    $roots.Add((Join-Path $HOME ".cursor-server\data\User\globalStorage"))
    $roots.Add((Join-Path $HOME "Library\Application Support\Code\User\globalStorage"))
    $roots.Add((Join-Path $HOME "Library\Application Support\CodeBuddy\User\globalStorage"))
    $roots.Add((Join-Path $HOME "Library\Application Support\Cursor\User\globalStorage"))

    $paths = New-Object System.Collections.Generic.List[string]
    foreach ($root in $roots) {
        foreach ($extId in @("tencent-cloud.coding-copilot", "tencent.planning-genie")) {
            $extDir = Join-Path $root $extId
            if (Test-Path -LiteralPath $extDir -PathType Container) {
                $paths.Add((Join-Path $extDir "settings\codebuddy_mcp_settings.json"))
            }
        }
    }
    return $paths.ToArray()
}

function Get-CodeBuddyConfigPaths {
    param([switch]$AllCliFiles)
    $cliPaths = Get-CodeBuddyCliCandidates
    if (-not $AllCliFiles) {
        $cliPaths = Get-FirstExistingConfig $cliPaths
    }
    $paths = @($cliPaths) + @(Get-CodeBuddyExtPaths)
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($path in $paths) {
        if ($seen.Add($path)) { $path }
    }
}

function Remove-DshBlock {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $lines = [regex]::Split($Text, '(?<=\n)')
    $kept = New-Object System.Collections.Generic.List[string]
    $skipping = $false
    foreach ($line in $lines) {
        if ($line -eq "") { continue }
        $trimmed = $line.Trim()
        if ($trimmed -eq $DSH_BEGIN) {
            $skipping = $true
            continue
        }
        if ($skipping) {
            if ($trimmed -eq $DSH_END) { $skipping = $false }
            continue
        }
        $kept.Add($line)
    }
    return (($kept -join "") -replace '(\r?\n)+$', '')
}

function Test-DshEmptyList {
    param([string]$Text)
    $parts = @()
    foreach ($line in ($Text -split "\r?\n")) {
        $t = $line.Trim()
        if ($t -ne "" -and -not $t.StartsWith("#")) {
            $parts += $t
        }
    }
    return (($parts -join "") -eq "[]")
}

function Format-DshBlock {
    param([string]$Token)
    $escUrl = $MCP_URL.Replace("'", "''")
    $escTok = $Token.Replace("'", "''")
    return "$DSH_BEGIN`n" +
           "- insert:`n" +
           "    - id: mcp-$SERVER_NAME`n" +
           "      name: '@deepseek-ai/dsh-mcp-client'`n" +
           "      config:`n" +
           "        serverName: $SERVER_NAME`n" +
           "        transport: streamable-http`n" +
           "        url: '$escUrl'`n" +
           "        headers:`n" +
           "          Authorization: 'Bearer $escTok'`n" +
           "$DSH_END`n"
}

function Get-DshToken {
    param([string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "" }
    $inBlock = $false
    foreach ($line in ($Text -split "\r?\n")) {
        $t = $line.Trim()
        if ($t -eq $DSH_BEGIN) { $inBlock = $true; continue }
        if ($inBlock) {
            if ($t -eq $DSH_END) { $inBlock = $false; continue }
            if ($line -match $TOKEN_RE) { return $Matches[1] }
        }
    }
    # 回退检查手动配置的 id: mcp-<SERVER_NAME>
    $inManual = $false
    $idPattern = '\bid\s*:\s*[''"]?mcp-' + [regex]::Escape($SERVER_NAME) + '[''"]?\s*$'
    foreach ($line in ($Text -split "\r?\n")) {
        if ($line -match $idPattern) { $inManual = $true; continue }
        if ($inManual) {
            if ($line -match '^\s*-\s+id\s*:' -or $line -match '^-\s+insert\s*:') { $inManual = $false; continue }
            if ($line -match $TOKEN_RE) { return $Matches[1] }
        }
    }
    return ""
}

function Find-JsonMcpServersToken {
    param([string]$ClientKey, [string]$Path, [string]$Section = 'mcpServers')
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    try {
        $tokens = (Read-JsoncDocument $Path).Tokens
        $index = 0
        foreach ($key in @($Section, $SERVER_NAME, 'headers', 'Authorization')) {
            if ($tokens[$index].Value -cne '{') { return $null }
            $comparer = if ($key -ceq 'Authorization') {
                [System.StringComparer]::OrdinalIgnoreCase
            } else {
                [System.StringComparer]::Ordinal
            }
            $properties = Get-JsoncProperties $tokens $index $comparer
            if (-not $properties.ContainsKey($key)) { return $null }
            $index = $properties[$key][1]
        }
        $value = $tokens[$index].Value
        if (-not $value.StartsWith('"')) { return $null }
        $tok = Extract-BearerToken (ConvertFrom-JsonString $value)
        if (-not [string]::IsNullOrEmpty($tok)) {
            return [PSCustomObject]@{ Client = $ClientKey; Token = $tok }
        }
        return $null
    } catch {
        return [PSCustomObject]@{ Client = "$ClientKey-unreadable"; Token = "-" }
    }
}

function Invoke-Config {
    param(
        [ValidateSet("find", "write", "remove")]
        [string]$Op,
        [string]$Token = "",
        [string[]]$Clients = @()
    )

    if ($Op -eq "find") {
        $results = New-Object System.Collections.Generic.List[object]
        $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
        $addResult = {
            param($item)
            if ($null -eq $item) { return }
            $sig = "$($item.Client)`t$($item.Token)"
            if ($seen.Add($sig)) { $results.Add($item) }
        }

        & $addResult (Find-JsonMcpServersToken "claude" $CLAUDE_JSON)

        if (Test-Path -LiteralPath $CODEX_TOML) {
            $found = Get-CodexToken ([System.IO.File]::ReadAllText($CODEX_TOML, [System.Text.Encoding]::UTF8))
            if (-not [string]::IsNullOrEmpty($found)) {
                & $addResult ([PSCustomObject]@{ Client = "codex"; Token = $found })
            }
        }

        & $addResult (Find-JsonMcpServersToken "cursor" $CURSOR_JSON)

        & $addResult (Find-JsonMcpServersToken "opencode" (Get-OpenCodeConfigPath) -Section "mcp")

        foreach ($cbPath in (Get-CodeBuddyConfigPaths)) {
            & $addResult (Find-JsonMcpServersToken "codebuddy" $cbPath)
        }

        if (Test-Path -LiteralPath $DSH_PATCH_YML) {
            $found = Get-DshToken ([System.IO.File]::ReadAllText($DSH_PATCH_YML, [System.Text.Encoding]::UTF8))
            if (-not [string]::IsNullOrEmpty($found)) {
                & $addResult ([PSCustomObject]@{ Client = "dsh"; Token = $found })
            }
        }

        return $results.ToArray()
    }

    $clientSet = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($c in $Clients) {
        if (-not [string]::IsNullOrWhiteSpace($c)) { [void]$clientSet.Add($c.Trim()) }
    }

    if ($clientSet.Contains("claude")) {
        $entry = if ($Op -eq "write") {
            [ordered]@{
                type    = "http"
                url     = $MCP_URL
                headers = [ordered]@{ Authorization = "Bearer $Token" }
            }
        }
        Edit-McpJsonc $CLAUDE_JSON "mcpServers" "claude" $entry
    }

    if ($clientSet.Contains("codex")) {
        if ($Op -eq "write" -or (Test-Path -LiteralPath $CODEX_TOML)) {
            $text = if (Test-Path -LiteralPath $CODEX_TOML) {
                [System.IO.File]::ReadAllText($CODEX_TOML, [System.Text.Encoding]::UTF8)
            } else { "" }
            $stripped = Remove-CodexSection $text
            if ($Op -eq "write") {
                $block = "[mcp_servers.$SERVER_NAME]`n" +
                         "url = `"$MCP_URL`"`n" +
                         "http_headers = { `"Authorization`" = `"Bearer $Token`" }`n" +
                         "enabled = true`n" +
                         "startup_timeout_sec = 30`n"
                $outText = if ($stripped) { "$stripped`n`n$block" } else { $block }
                Save-TextAtomic $CODEX_TOML $outText -Client "codex"
            } elseif ($stripped -ne ($text -replace '(\r?\n)+$', '')) {
                $outText = if ($stripped) { "$stripped`n" } else { "" }
                Save-TextAtomic $CODEX_TOML $outText
            }
        }
    }

    if ($clientSet.Contains("cursor")) {
        $entry = if ($Op -eq "write") {
            [ordered]@{
                url     = $MCP_URL
                headers = [ordered]@{ Authorization = "Bearer $Token" }
            }
        }
        Edit-McpJsonc $CURSOR_JSON "mcpServers" "cursor" $entry
    }

    if ($clientSet.Contains("opencode")) {
        if ($Op -eq "write") {
            $entry = [ordered]@{
                type    = "remote"
                url     = $MCP_URL
                enabled = $true
                oauth   = $false
                timeout = 30000
                headers = [ordered]@{ Authorization = "Bearer $Token" }
            }
            Edit-McpJsonc (Get-OpenCodeConfigPath) "mcp" "opencode" $entry
        } else {
            $paths = @($OPENCODE_JSON) + @(Get-OpenCodeGlobalConfigPaths)
            foreach ($path in ($paths | Select-Object -Unique)) {
                Edit-McpJsonc $path "mcp" "opencode" $null
            }
        }
    }

    if ($clientSet.Contains("codebuddy")) {
        $entry = if ($Op -eq "write") {
            [ordered]@{
                type    = "http"
                url     = $MCP_URL
                headers = [ordered]@{ Authorization = "Bearer $Token" }
            }
        }
        foreach ($cbPath in (Get-CodeBuddyConfigPaths -AllCliFiles:($Op -eq 'remove'))) {
            Edit-McpJsonc $cbPath "mcpServers" "codebuddy" $entry
        }
    }

    if ($clientSet.Contains("dsh")) {
        if ($Op -eq "write" -or (Test-Path -LiteralPath $DSH_PATCH_YML)) {
            $text = if (Test-Path -LiteralPath $DSH_PATCH_YML) {
                [System.IO.File]::ReadAllText($DSH_PATCH_YML, [System.Text.Encoding]::UTF8)
            } else { "" }
            $stripped = Remove-DshBlock $text
            if ($Op -eq "write") {
                $base = if ([string]::IsNullOrWhiteSpace($stripped) -or (Test-DshEmptyList $stripped)) {
                    ""
                } else {
                    "$stripped`n`n"
                }
                Save-TextAtomic $DSH_PATCH_YML ($base + (Format-DshBlock $Token)) -Client "dsh"
            } elseif ($stripped -ne ($text -replace '(\r?\n)+$', '')) {
                $outText = if ([string]::IsNullOrWhiteSpace($stripped)) { "[]`n" } else { "$stripped`n" }
                Save-TextAtomic $DSH_PATCH_YML $outText
            }
        }
    }
}

function Test-HasExtension {
    param([string]$Prefix)
    $dirs = @(
        (Join-Path $HOME ".vscode-server\extensions"),
        (Join-Path $HOME ".vscode\extensions"),
        (Join-Path $HOME ".cursor-server\extensions"),
        (Join-Path $HOME ".cursor\extensions")
    )
    foreach ($dir in $dirs) {
        if ((Test-Path -LiteralPath $dir) -and (Get-ChildItem -LiteralPath $dir -Filter "$Prefix*" -ErrorAction SilentlyContinue | Select-Object -First 1)) {
            return $true
        }
    }
    return $false
}

function Resolve-ClientList {
    param([string]$Raw)
    if ([string]::IsNullOrWhiteSpace($Raw)) { return $null }
    $items = @($Raw -split '[,\s]+' | Where-Object { $_ -ne "" })
    $out = New-Object System.Collections.Generic.List[string]
    $seen = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($item in $items) {
        $canon = $item.Trim().ToLowerInvariant()
        if ($canon -eq "all") { return ,$ALL_CLIENTS }
        if ($canon -eq "deepseek-harness") { $canon = "dsh" }
        if ($canon -notin $ALL_CLIENTS) { return $null }
        if ($seen.Add($canon)) { $out.Add($canon) }
    }
    if ($out.Count -eq 0) { return $null }
    return ,$out.ToArray()
}

function Get-DetectedClients {
    if (-not [string]::IsNullOrWhiteSpace($env:MCP_CLIENTS)) {
        $resolved = Resolve-ClientList $env:MCP_CLIENTS
        if ($null -eq $resolved) {
            throw "无效的 MCP_CLIENTS：$($env:MCP_CLIENTS)"
        }
        return $resolved
    }
    $found = New-Object System.Collections.Generic.List[string]
    if ((Get-Command claude -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath $CLAUDE_JSON) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".claude")) -or
        (Test-HasExtension "anthropic.claude-code")) {
        $found.Add("claude")
    }
    if ((Get-Command codex -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath $CODEX_HOME) -or
        (Test-HasExtension "openai.chatgpt")) {
        $found.Add("codex")
    }
    if ((Get-Command cursor -ErrorAction SilentlyContinue) -or
        (Get-Command cursor-agent -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".cursor")) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".cursor-server")) -or
        ($env:LOCALAPPDATA -and (Test-Path -LiteralPath (Join-Path $env:LOCALAPPDATA "Programs\cursor\Cursor.exe")))) {
        $found.Add("cursor")
    }
    $opencodeDir = Split-Path -Path $OPENCODE_JSON -Parent
    if ((Get-Command opencode -ErrorAction SilentlyContinue) -or
        (-not [string]::IsNullOrWhiteSpace($env:OPENCODE_CONFIG)) -or
        ($opencodeDir -and (Test-Path -LiteralPath $opencodeDir)) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".opencode")) -or
        (Test-HasExtension "sst-dev.opencode") -or
        (Test-HasExtension "koarthur.opencode")) {
        $found.Add("opencode")
    }
    if ((Get-Command codebuddy -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".codebuddy")) -or
        (Test-Path -LiteralPath (Join-Path $HOME ".codebuddy.json")) -or
        (Test-HasExtension "tencent-cloud.coding-copilot") -or
        (Test-HasExtension "tencent.planning-genie") -or
        (@(Get-CodeBuddyExtPaths).Count -gt 0)) {
        $found.Add("codebuddy")
    }
    if ((Get-Command dsh -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath $DSH_HOME_DIR) -or
        (Test-HasExtension "jager.dsh-vscode")) {
        $found.Add("dsh")
    }
    return $found.ToArray()
}

function Get-LocalToken {
    try {
        $entries = @(Invoke-Config -Op "find")
        foreach ($e in $entries) {
            if ($e.Client -in $ALL_CLIENTS) {
                return $e.Token
            }
        }
    } catch {}
    return ""
}

function Get-ClientLabel {
    param([string]$Client)
    switch ($Client) {
        "claude"    { return "Claude Code" }
        "codex"     { return "Codex" }
        "cursor"    { return "Cursor" }
        "opencode"  { return "OpenCode" }
        "codebuddy" { return "CodeBuddy" }
        "dsh"       { return "DeepSeek Harness" }
        default     { return $Client }
    }
}

function Write-OtherHarnesses {
    param([string]$Token = "")
    $displayToken = if ($Token) { $Token } else { "<你的 token>" }
    $header = @"
# 其他客户端（如 Windsurf / Cline 等）请按各自文档添加 HTTP MCP 服务器：
#   地址：$MCP_URL
#   请求头：Authorization: Bearer $displayToken
# 注意：发评论必须在 elicitation 表单中输入与 Token 账号匹配的用户名（不支持的客户端无法发评论）；附件上传不弹确认框，但仍校验权限。
"@
    $links = @'
__HARNESS_LINKS__
'@
    Write-Output $header
    Write-Output $links
}

function Show-Config {
    param([string]$Token = "")
    $displayToken = if ($Token) { $Token } else { "<你的 token>" }
    if ($displayToken -match '^rsmcp_[A-Za-z0-9_-]+$') {
        Show-Token $displayToken
    }
    $snippet = @"

# ── 1. Claude Code（CLI 与 VS Code 扩展通用）──────────────────────────
# 在 ~/.claude.json 顶层 "mcpServers" 中加入：
"$SERVER_NAME": {
  "type": "http",
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $displayToken" }
}
# 或直接执行 CLI 命令：
claude mcp remove $SERVER_NAME -s user 2>`$null; claude mcp add --transport http --scope user $SERVER_NAME $MCP_URL --header "Authorization: Bearer $displayToken"

# ── 2. Codex（CLI 与 VS Code 扩展通用）────────────────────────────────
# 在 ~/.codex/config.toml 中加入（如已存在同名段落请先删除）：
# 若完全访问下没有用户名弹窗，将下面审批键放在文件顶层、所有表头之前，替换已有同名键：
# approval_policy = { granular = { sandbox_approval = false, rules = false, mcp_elicitations = true } }
# 保留原 sandbox_mode；需要完全访问时可设为 "danger-full-access"。
# 重启后开启新会话，核对实际策略；--yolo 或客户端“完全访问”预设可能再次覆盖为 never。
[mcp_servers.$SERVER_NAME]
url = "$MCP_URL"
http_headers = { "Authorization" = "Bearer $displayToken" }
enabled = true
startup_timeout_sec = 30

# ── 3. Cursor（IDE 与 Cursor CLI 通用）────────────────────────────────
# 在 ~/.cursor/mcp.json 的 "mcpServers" 中加入：
"$SERVER_NAME": {
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $displayToken" }
}

# ── 4. OpenCode（CLI 与 VS Code 扩展通用）─────────────────────────────
# 在 OpenCode 生效的全局配置（例如 ~/.config/opencode/opencode.jsonc）的 "mcp" 中加入：
"$SERVER_NAME": {
  "type": "remote",
  "url": "$MCP_URL",
  "enabled": true,
  "oauth": false,
  "timeout": 30000,
  "headers": { "Authorization": "Bearer $displayToken" }
}

# ── 5. CodeBuddy（CLI 与 VS Code 扩展 / CodeBuddy IDE）────────────────
# CLI：在 ~/.codebuddy/.mcp.json 的 "mcpServers" 中加入（VS Code 扩展则在 codebuddy_mcp_settings.json）：
"$SERVER_NAME": {
  "type": "http",
  "url": "$MCP_URL",
  "headers": { "Authorization": "Bearer $displayToken" }
}
# 或执行 CLI 命令：
codebuddy mcp add-json --scope user $SERVER_NAME '{"type":"http","url":"$MCP_URL","headers":{"Authorization":"Bearer $displayToken"}}'

# ── 6. DeepSeek Harness（CLI / Web / VS Code 扩展通用）────────────────
# 合并到 ~/.dsh/cordis.patch.yml（对全部 profile 生效，重启 dsh 后加载）：
- insert:
    - id: mcp-$SERVER_NAME
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: $SERVER_NAME
        transport: streamable-http
        url: '$MCP_URL'
        headers:
          Authorization: 'Bearer $displayToken'

"@
    Write-Output $snippet
    Write-OtherHarnesses $displayToken
}

function Write-LocalConfig {
    param([string]$Token)
    $clients = @()
    try {
        $clients = @(Get-DetectedClients)
    } catch {
        Show-Token $Token
        Die "客户端列表无效或未能检测本机客户端（支持：claude, codex, cursor, opencode, codebuddy, dsh, all）。请保存上方 Token，之后可直接复用。"
    }

    if ($clients.Count -eq 0) {
        Warn "未在本机检测到 Claude Code / Codex / Cursor / OpenCode / CodeBuddy / DeepSeek Harness。"
        if (Confirm-YesNo "是否仍为全部 6 款工具预先写入配置文件？[y/N] " "n") {
            $clients = $ALL_CLIENTS
        } else {
            Show-Config $Token
            return
        }
    }

    $sysUser = Get-CurrentSystemUser
    if (-not [string]::IsNullOrEmpty($script:Username) -and ($script:Username -ne $sysUser)) {
        Write-Host ""
        Warn "⚠️  账号不一致提醒："
        Warn "   配置将写入当前系统用户（$(if ($sysUser) { $sysUser } else { '?' })）的目录 $HOME 下，"
        Warn "   后续该用户运行的 Coding 工具都将以【$($script:Username)】的身份调用发布系统！"
    }

    try {
        $writtenFiles = @(Invoke-Config -Op "write" -Token $Token -Clients $clients)
    } catch {
        Show-Token $Token
        Die "本机配置写入失败（$($_.Exception.Message)）。请保存上方 Token，之后可直接复用。"
    }

    Write-Host ""
    foreach ($file in $writtenFiles) {
        Write-Colored '✔ ' Green -NoNewline
        Say ("{0,-16} 配置已写入  {1}" -f (Get-ClientLabel $file.Client), $file.Path)
    }

    if ($clients -contains "cursor") {
        Say 'Cursor：IDE 与 Cursor CLI (agent) 共用 ~/.cursor/mcp.json；若未立即出现工具，请在 Cursor Settings → MCP 中刷新或启用。'
    }
    if ($clients -contains "dsh") {
        Say "DeepSeek Harness：已写入全局 patch（$DSH_PATCH_YML），重启 dsh / dsh web 后生效（VS Code 扩展会自动重连），工具名为 mcp__${SERVER_NAME}__*。"
    }

    if (($clients -contains "claude") -and (Get-Process -Name "claude" -ErrorAction SilentlyContinue)) {
        Write-Host ""
        Warn "⚠️  进程冲突提醒：检测到 Claude Code 正在运行！"
        Warn "   它退出时可能会覆盖 ~/.claude.json。若重启后未看到 $SERVER_NAME，"
        Warn "   请先彻底关闭 Claude Code，再重新运行本脚本。"
    }

    Show-Token $Token
}

# ── 子命令逻辑 ────────────────────────────────────────────────────────
function Confirm-Regenerate {
    if (-not (Confirm-YesNo '其他机器上的旧 Token 将立即失效，是否继续？[y/N]: ' 'n')) {
        Die "已取消操作"
    }
}

function Invoke-UseToken {
    $token = $env:MCP_TOKEN
    if ([string]::IsNullOrEmpty($token)) {
        $token = Ask-Secret '请粘贴你的 MCP Token（输入时不显示）: '
    }
    if ($token -notmatch '^rsmcp_[A-Za-z0-9_-]+$') {
        Die "Token 格式不正确（应以 rsmcp_ 开头）"
    }
    $resolved = Resolve-ClientList $env:MCP_CLIENTS
    if ($null -eq $resolved) {
        Die "请通过环境变量指定有效目标客户端，例如：`$env:MCP_CLIENTS='claude,cursor'（支持：claude / codex / cursor / opencode / codebuddy / dsh / all）"
    }
    if (-not (Test-TokenWorks $token)) {
        Die "Token 无效、已吊销，或无法连接到服务"
    }
    Say "服务地址：$MCP_URL"
    Write-LocalConfig $token
}

function Invoke-Configure {
    Update-ServerStatus
    Say "服务地址：$MCP_URL"
    $token = ""

    if (-not [string]::IsNullOrEmpty($script:ActivePrefix)) {
        $found = Get-LocalToken
        if (-not [string]::IsNullOrEmpty($found) -and
            $found.StartsWith($script:ActivePrefix, [System.StringComparison]::Ordinal) -and
            (Test-TokenWorks $found)) {
            $token = $found
            Success "✔ 检测到本机已有有效 Token，将直接复用"
        } else {
            Write-Host ""
            Warn "⚠️  检测到服务端已存在有效 Token："
            Warn "   $($script:ActiveLine)"
            Warn "   • 若你在其他机器配置过，请粘贴该 Token 以实现多机共用；"
            Warn "   • 若直接回车重新生成，其他机器上的旧 Token 将立即失效！"
            $pasted = Ask-Secret '请粘贴已有 Token 复用（或直接回车重新生成）: '
            if (-not [string]::IsNullOrEmpty($pasted)) {
                if (-not ($pasted.StartsWith($script:ActivePrefix, [System.StringComparison]::Ordinal) -and (Test-TokenWorks $pasted))) {
                    Die "粘贴的 Token 与当前服务端有效前缀（$($script:ActivePrefix)…）不匹配或已失效"
                }
                $token = $pasted
            }
        }
    }

    if ([string]::IsNullOrEmpty($token)) {
        if (-not [string]::IsNullOrEmpty($script:ActivePrefix)) {
            Confirm-Regenerate
        }
        $token = ((Invoke-Api "regenerate") -split "\r?\n")[0]
        Success "✔ 已生成新 Token"
    }
    Write-LocalConfig $token
}

function Invoke-Status {
    Update-ServerStatus
    Write-Host ""
    if (-not [string]::IsNullOrEmpty($script:ActivePrefix)) {
        Say "☁️  服务端有效 Token：$($script:ActiveLine)"
    } else {
        Warn "☁️  服务端状态：当前没有任何有效 Token"
    }

    $entries = @()
    try {
        $entries = @(Invoke-Config -Op "find")
    } catch {
        Die "无法读取本机配置，无法查询本机 Token。"
    }

    $found = $false
    $currentToken = ""
    foreach ($entry in $entries) {
        $found = $true
        if ($entry.Client.EndsWith("-unreadable")) {
            $baseClient = $entry.Client.Substring(0, $entry.Client.Length - 11)
            Warn "💻 本机 $(Get-ClientLabel $baseClient)：配置文件 JSON 格式错误，无法解析"
        } else {
            $isValid = (-not [string]::IsNullOrEmpty($script:ActivePrefix)) -and
                       $entry.Token.StartsWith($script:ActivePrefix, [System.StringComparison]::Ordinal)
            if ($isValid) {
                Success "💻 本机 $(Get-ClientLabel $entry.Client)：（有效）"
                $currentToken = $entry.Token
            } else {
                Warn "💻 本机 $(Get-ClientLabel $entry.Client)：$($entry.Token)（已失效）"
            }
        }
    }

    if (-not $found) {
        Say "💻 本机状态：尚未配置 $SERVER_NAME"
        Dim "   提示：服务端仅保存摘要，无法反查完整 Token；如需复用请从其他已配置机器复制。"
    } elseif (-not [string]::IsNullOrEmpty($currentToken)) {
        Show-Token $currentToken
    }
}

function Invoke-Regenerate {
    Update-ServerStatus
    Say "服务地址：$MCP_URL"
    if (-not [string]::IsNullOrEmpty($script:ActivePrefix)) {
        Write-Host ""
        Warn "🚨 高危操作提醒：重新生成 Token"
        Warn "   当前有效 Token：$($script:ActiveLine)"
        Warn "   一旦重新生成，上述旧 Token 将在【所有已配置机器】上立即失效！"
        Confirm-Regenerate
    }
    $token = ((Invoke-Api "regenerate") -split "\r?\n")[0]
    Success "✔ 已生成新 Token"
    if (Confirm-YesNo "是否立即写入本机配置？[Y/n] " "y") {
        Write-LocalConfig $token
    } else {
        Show-Config $token
    }
}

function Invoke-Uninstall {
    Invoke-Config -Op "remove" -Token "" -Clients $ALL_CLIENTS
    Success "✔ 已从本机配置文件中移除 $SERVER_NAME"
    Dim "  （被修改的原配置文件已自动备份为 *.bak-release-mcp）"
    Write-Host ""
    Warn "ℹ️  说明：此操作仅移除本机配置，Token 本身依然有效。"
    Warn "   如需彻底作废 Token，请重新运行脚本选择「3) 重新生成 token」，或在网站「AI助手 → MCP」页面手动吊销。"
}

# ── 主入口 ────────────────────────────────────────────────────────────
try {
    $resolvedAction = if (-not [string]::IsNullOrWhiteSpace($Action)) {
        $Action.Trim()
    } elseif (-not [string]::IsNullOrWhiteSpace($env:MCP_ACTION)) {
        $env:MCP_ACTION.Trim()
    } else {
        ""
    }

    if ([string]::IsNullOrEmpty($resolvedAction)) {
        Write-Host ""
        Bold "══════════════ HPC 发布系统 MCP 配置工具 ══════════════"
        Say  "  1) 一键配置本机      （自动检测客户端并写入配置）"
        Say  "  2) 查询 Token 状态   （对比服务端与本机配置，显示本机完整 Token）"
        Say  "  3) 重新生成 Token    （⚠️ 会使其他机器的旧 Token 失效）"
        Say  "  4) 卸载本机配置      （移除本机配置文件中的 MCP 项）"
        Bold "═══════════════════════════════════════════════════════"
        $resolvedAction = Ask "请选择操作编号 [默认 1]: "
        if ([string]::IsNullOrWhiteSpace($resolvedAction)) { $resolvedAction = "1" }
    }

    switch ($resolvedAction.ToLowerInvariant()) {
        "use-token"  { Invoke-UseToken }
        "1"          { Invoke-Configure }
        "configure"  { Invoke-Configure }
        "2"          { Invoke-Status }
        "status"     { Invoke-Status }
        "3"          { Invoke-Regenerate }
        "regenerate" { Invoke-Regenerate }
        "4"          { Invoke-Uninstall }
        "uninstall"  { Invoke-Uninstall }
        default      { Die "未知操作：$resolvedAction（可用参数：configure / use-token / status / regenerate / uninstall）" }
    }
} catch {
    if ($_.Exception.Message -ne $script:ABORT_TAG) {
        Write-Colored "✘ 发生未预期错误：$($_.Exception.Message)" Red
    }
    if ($MyInvocation.MyCommand -is [System.Management.Automation.ExternalScriptInfo]) {
        exit 1
    }
}
