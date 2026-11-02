<#
.SYNOPSIS
    Run the whole pipeline once: source DB up -> warehouse setup -> CSV ingest ->
    Postgres ingest (initial snapshot or incremental sync) -> dbt build.

.DESCRIPTION
    Safe to re-run at any time. Every step is idempotent, and the Postgres step picks
    itself: an initial snapshot when the warehouse has no watermarks yet (first run, or
    analytics.duckdb was deleted), an incremental sync otherwise.

    Stops at the first failing step. Close anything holding warehouse\analytics.duckdb
    open (a Python shell, the DuckDB CLI, a BI tool) first: DuckDB allows one writer.

.PARAMETER FullRefresh
    Rebuild from scratch even though watermarks exist: re-snapshot Postgres and run
    dbt build --full-refresh. Use after a bad sync, or to pick up hard deletes.

.EXAMPLE
    .\run_pipeline.ps1
    .\run_pipeline.ps1 -FullRefresh
#>
param(
    [switch]$FullRefresh
)

# For PowerShell's own commands. Native programs are checked by exit code in Invoke-Step.
$ErrorActionPreference = 'Stop'

# Paths are anchored to this script, so it works from any current directory.
$Root   = $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Dbt    = Join-Path $Root '.venv\Scripts\dbt.exe'

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command, [string]$WorkingDirectory = $Root)

    Write-Host "`n=== $Name" -ForegroundColor Cyan
    $started = Get-Date
    Push-Location $WorkingDirectory
    try {
        # Native programs are judged by exit code only. Under 'Stop', Windows PowerShell 5.1
        # turns anything they write to stderr (docker's progress messages, Python warnings)
        # into a terminating error whenever output is redirected, e.g. by a scheduler.
        $ErrorActionPreference = 'Continue'
        & $Command
        if ($LASTEXITCODE -ne 0) {
            throw "Step '$Name' failed with exit code $LASTEXITCODE."
        }
    }
    finally {
        Pop-Location
    }
    Write-Host ("    done in {0:N1}s" -f ((Get-Date) - $started).TotalSeconds) -ForegroundColor DarkGray
}

$pipelineStarted = Get-Date

Invoke-Step 'Source database (Postgres in Docker)' {
    docker compose up -d --wait
}

Invoke-Step 'Warehouse setup (file, schemas, meta.sync_state)' {
    & $Python ingestion\setup_warehouse.py
}

Invoke-Step 'Ingest campaign CSVs -> bronze.raw_campaigns' {
    & $Python ingestion\ingest_csv.py
}

# Snapshot or incremental: decided by the warehouse's own state, not by a flag someone has
# to remember. No watermarks means Bronze has never been loaded.
$watermarks = & $Python -c "import duckdb; print(duckdb.connect(r'$Root\warehouse\analytics.duckdb', read_only=True).execute('select count(*) from meta.sync_state').fetchone()[0])"
if ($LASTEXITCODE -ne 0) { throw 'Could not read meta.sync_state.' }

if ($FullRefresh -or [int]$watermarks -eq 0) {
    $reason = if ($FullRefresh) { '-FullRefresh' } else { 'no watermarks yet' }
    Invoke-Step "Ingest Postgres -> bronze: initial snapshot ($reason)" {
        & $Python ingestion\initial_snapshot.py
    }
}
else {
    Invoke-Step 'Ingest Postgres -> bronze: incremental sync' {
        & $Python ingestion\incremental_sync.py
    }
}

Invoke-Step 'dbt build (Silver + Gold models and tests)' {
    if ($FullRefresh) { & $Dbt build --full-refresh } else { & $Dbt build }
} -WorkingDirectory (Join-Path $Root 'dbt_project')

Write-Host ("`nPipeline finished in {0:N1}s" -f ((Get-Date) - $pipelineStarted).TotalSeconds) -ForegroundColor Green
