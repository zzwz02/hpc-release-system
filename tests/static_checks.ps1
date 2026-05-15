$ErrorActionPreference = "Stop"

function Assert-Contains {
    param(
        [string]$Path,
        [string]$Pattern
    )
    if (-not (Select-String -LiteralPath $Path -Pattern $Pattern -Encoding UTF8 -Quiet)) {
        throw "Missing pattern '$Pattern' in $Path"
    }
}

Assert-Contains "index.html" "/api/state"
Assert-Contains "index.html" "/api/import-initial"
Assert-Contains "index.html" "/api/app-info"
Assert-Contains "index.html" "/api/apps/update"
Assert-Contains "index.html" "/api/qa/status"
Assert-Contains "index.html" "/api/qa/upload-log"
Assert-Contains "index.html" "/api/login"
Assert-Contains "index.html" "/api/releases/final-lock"
Assert-Contains "index.html" "/api/releases/final-unlock"
Assert-Contains "index.html" "/api/releases/deadlines"
Assert-Contains "index.html" "/api/test-scope.csv"
Assert-Contains "index.html" "test_cmd"
Assert-Contains "index.html" "app_info"
Assert-Contains "index.html" "owner_added"
Assert-Contains "release_system/core.py" "def apply_app_info"
Assert-Contains "release_system/core.py" "def add_new_app_request"
Assert-Contains "release_system/core.py" "def qa_set_status"
Assert-Contains "release_system/core.py" "def qa_upload_log"
Assert-Contains "release_system/core.py" "def export_test_scope_csv"
Assert-Contains "release_system/core.py" "def final_lock_release"
Assert-Contains "release_system/core.py" "def final_unlock_release"
Assert-Contains "release_system/core.py" "def current_phase"
Assert-Contains "release_system/core.py" "def authenticate"
Assert-Contains "release_system/core.py" "def gerrit_push_plan"
Assert-Contains "release_system/core.py" "def parse_csv_text"
Assert-Contains "server.py" "ThreadingHTTPServer"
Assert-Contains "server.py" "/api/gerrit/plan"

Write-Output 'STATIC_CHECKS_OK'
