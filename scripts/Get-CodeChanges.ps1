<#
.SYNOPSIS
    Extracts Git diff changes between target and source branches, filtering out noise and computing valid modified line ranges.

.DESCRIPTION
    Get-CodeChanges.ps1 performs a Git diff between the PR target branch and source branch.
    It filters out lockfiles, binaries, minified files, and generated directories to prevent token limits.
    For every included file, it parses unified diff hunks (`@@ -old,count +new,count @@`) to calculate exactly
    which right-side line numbers (`ValidLines`) were added or modified so review comments attach accurately.

.PARAMETERS
    -SourceBranch: The HEAD branch of the pull request.
    -TargetBranch: The BASE branch of the pull request.
    -OutputPath: Path to save the structured JSON diff (default: changes.json).
    -MaxDiffLinesPerFile: Truncation threshold for diff hunks (default: 800 lines).
#>

param (
    [string]$SourceBranch = $env:GITHUB_HEAD_REF,
    [string]$TargetBranch = $env:GITHUB_BASE_REF,
    [string]$OutputPath = "changes.json",
    [int]$MaxDiffLinesPerFile = 800
)

# Ensure branch defaults if running locally or outside Actions env vars
if ([string]::IsNullOrWhiteSpace($TargetBranch)) {
    $TargetBranch = "main"
}
if ([string]::IsNullOrWhiteSpace($SourceBranch)) {
    # If no source branch given, compare against current HEAD
    $SourceBranch = "HEAD"
}

Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "    AI Review: Get-CodeChanges" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Comparing: $TargetBranch ... $SourceBranch" -ForegroundColor Yellow

# Resolve Git branch refs cleanly (handling origin/ prefix when checked out in CI)
function Resolve-GitRef {
    param([string]$RefName)
    if ($RefName -eq "HEAD") { return "HEAD" }
    
    # Check if origin/RefName exists
    $originRef = "origin/$RefName"
    if (git rev-parse --verify --quiet $originRef 2>$null) {
        return $originRef
    }
    # Check if local RefName exists
    if (git rev-parse --verify --quiet $RefName 2>$null) {
        return $RefName
    }
    # Fallback to provided ref
    return $RefName
}

$resolvedTarget = Resolve-GitRef $TargetBranch
$resolvedSource = Resolve-GitRef $SourceBranch

Write-Host "Resolved Target Ref: $resolvedTarget" -ForegroundColor Gray
Write-Host "Resolved Source Ref: $resolvedSource" -ForegroundColor Gray

# Determine diff command (prefer triple-dot merge-base diff so we only inspect PR changes)
$diffRange = "$resolvedTarget...$resolvedSource"
git diff --quiet $diffRange 2>$null
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne 1) {
    Write-Host "Merge-base diff ($diffRange) failed or not available. Falling back to direct diff ($resolvedTarget..$resolvedSource)." -ForegroundColor Yellow
    $diffRange = "$resolvedTarget..$resolvedSource"
}

# Get list of modified/added files
$rawFileList = git diff --name-only --diff-filter=ACMR $diffRange
if (-not $rawFileList) {
    Write-Host "No changed files detected between $resolvedTarget and $resolvedSource." -ForegroundColor Green
    $emptyResult = @{
        sourceBranch = $SourceBranch
        targetBranch = $TargetBranch
        totalFilesChanged = 0
        changedFiles = @()
    }
    $emptyResult | ConvertTo-Json -Depth 5 | Set-Content -Path $OutputPath -Encoding utf8
    exit 0
}

# Excluded file patterns (lockfiles, minified files, binaries, build outputs)
$excludePatterns = @(
    "*.lock", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock",
    "*.min.js", "*.min.css", "*.map", "*.bundle.js",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico", "*.webp",
    "*.pdf", "*.zip", "*.tar", "*.gz", "*.woff", "*.woff2", "*.ttf",
    "bin/*", "obj/*", "dist/*", "build/*", ".git/*"
)

function Test-IsExcluded {
    param([string]$FilePath)
    foreach ($pattern in $excludePatterns) {
        if ($FilePath -like $pattern -or $FilePath -like "*/$pattern") {
            return $true
        }
    }
    return $false
}

$changedFilesList = @()

foreach ($file in $rawFileList) {
    if ([string]::IsNullOrWhiteSpace($file)) { continue }
    
    if (Test-IsExcluded $file) {
        Write-Host "  [SKIP] Excluded pattern: $file" -ForegroundColor DarkGray
        continue
    }

    # Extract unified diff patch for this file
    $patchLines = git diff --unified=3 $diffRange -- "$file"
    if (-not $patchLines -or $patchLines.Count -eq 0) {
        continue
    }

    # Truncate if diff exceeds max lines
    if ($patchLines.Count -gt $MaxDiffLinesPerFile) {
        Write-Host "  [TRUNCATE] $file ($($patchLines.Count) lines -> $MaxDiffLinesPerFile max)" -ForegroundColor Yellow
        $patchLines = $patchLines[0..($MaxDiffLinesPerFile - 1)] + "`n... [DIFF TRUNCATED BY PIPELINE DUE TO LENGTH] ..."
    } else {
        Write-Host "  [INSPECT] $file ($($patchLines.Count) patch lines)" -ForegroundColor Cyan
    }

    $patchText = $patchLines -join "`n"

    # Parse diff hunks to compute exact valid right-side line numbers (`ValidLines`)
    # Hunk header: @@ -oldStart,oldLen +newStart,newLen @@
    $validLines = [System.Collections.Generic.List[int]]::new()
    $currentNewLine = 0
    $inHunk = $false

    foreach ($line in $patchLines) {
        if ($line -match '^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@') {
            $currentNewLine = [int]$matches[1]
            $inHunk = $true
            continue
        }

        if (-not $inHunk) { continue }

        if ($line.StartsWith("+") -and -not $line.StartsWith("+++")) {
            # Added or modified line on the right side
            $validLines.Add($currentNewLine)
            $currentNewLine++
        } elseif ($line.StartsWith("-") -and -not $line.StartsWith("---")) {
            # Deleted line from old file; right-side line count doesn't advance
        } elseif ($line.StartsWith(" ")) {
            # Unchanged context line
            $currentNewLine++
        } elseif ($line.StartsWith("\")) {
            # "\ No newline at end of file"
        }
    }

    $changedFilesList += @{
        filePath = $file
        validLines = $validLines
        patchText = $patchText
    }
}

$resultObject = @{
    sourceBranch = $SourceBranch
    targetBranch = $TargetBranch
    totalFilesChanged = $changedFilesList.Count
    changedFiles = $changedFilesList
}

$jsonOutput = $resultObject | ConvertTo-Json -Depth 10
Set-Content -Path $OutputPath -Value $jsonOutput -Encoding utf8

Write-Host "`nSuccessfully extracted diffs for $($changedFilesList.Count) files -> Saved to $OutputPath" -ForegroundColor Green
Write-Host "==========================================`n" -ForegroundColor Cyan
