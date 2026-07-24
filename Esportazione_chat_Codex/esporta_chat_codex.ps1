param(
    [Parameter(Mandatory = $true)]
    [string]$SessionPath,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"

function Get-CleanUserText {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return ""
    }

    $marker = "## My request for Codex:"
    $markerIndex = $Text.IndexOf($marker, [System.StringComparison]::Ordinal)
    if ($markerIndex -ge 0) {
        return $Text.Substring($markerIndex + $marker.Length).Trim()
    }

    return $Text.Trim()
}

function Test-IsInternalUserRecord {
    param([string]$Text)

    $trimmed = $Text.TrimStart()
    return (
        $trimmed.StartsWith("<recommended_plugins>", [System.StringComparison]::Ordinal) -or
        $trimmed.StartsWith("<environment_context>", [System.StringComparison]::Ordinal) -or
        $trimmed.StartsWith("<turn_aborted>", [System.StringComparison]::Ordinal)
    )
}

if (-not (Test-Path -LiteralPath $SessionPath -PathType Leaf)) {
    throw "Sessione Codex non trovata: $SessionPath"
}

$sessionFullPath = (Resolve-Path -LiteralPath $SessionPath).Path
$outputFullPath = [System.IO.Path]::GetFullPath($OutputPath)
$outputDirectory = [System.IO.Path]::GetDirectoryName($outputFullPath)

if (-not [string]::IsNullOrWhiteSpace($outputDirectory)) {
    [System.IO.Directory]::CreateDirectory($outputDirectory) | Out-Null
}

$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$sections = New-Object System.Collections.Generic.List[string]
$messageCount = 0

$sections.Add("# Esportazione conversazione Codex")
$sections.Add("")
$sections.Add("Sessione locale: ``$sessionFullPath``")
$sections.Add("")
$sections.Add("Esportazione generata il $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss'). Sono inclusi soltanto i messaggi visibili di utente e Codex. Il contesto automatico dell'IDE, le chiamate agli strumenti e i dati tecnici interni sono esclusi.")

$fileStream = New-Object System.IO.FileStream(
    $sessionFullPath,
    [System.IO.FileMode]::Open,
    [System.IO.FileAccess]::Read,
    [System.IO.FileShare]::ReadWrite
)
$reader = New-Object System.IO.StreamReader($fileStream, $utf8NoBom, $true)

try {
    while (($line = $reader.ReadLine()) -ne $null) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }

        try {
            $record = $line | ConvertFrom-Json
        }
        catch {
            continue
        }

        if ($record.type -ne "response_item" -or
            $record.payload.type -ne "message" -or
            $record.payload.role -notin @("user", "assistant")) {
            continue
        }

        $parts = New-Object System.Collections.Generic.List[string]
        foreach ($content in @($record.payload.content)) {
            if ($content.type -in @("input_text", "output_text") -and
                -not [string]::IsNullOrWhiteSpace([string]$content.text)) {
                $parts.Add([string]$content.text)
            }
            elseif ($record.payload.role -eq "user" -and $content.type -eq "input_image") {
                $parts.Add("[Immagine allegata non incorporata nell'esportazione Markdown]")
            }
        }

        if ($parts.Count -eq 0) {
            continue
        }

        $text = ($parts -join "`r`n`r`n").Trim()
        if ($record.payload.role -eq "user") {
            if (Test-IsInternalUserRecord -Text $text) {
                continue
            }
            $text = Get-CleanUserText -Text $text
            $speaker = "Utente"
        }
        elseif ($record.payload.phase -eq "commentary") {
            $speaker = "Codex (aggiornamento)"
        }
        else {
            $speaker = "Codex"
        }

        if ([string]::IsNullOrWhiteSpace($text)) {
            continue
        }

        $timestamp = ""
        try {
            $timestamp = ([DateTimeOffset]::Parse([string]$record.timestamp)).ToLocalTime().ToString("yyyy-MM-dd HH:mm:ss")
        }
        catch {
            $timestamp = [string]$record.timestamp
        }

        $sections.Add("")
        $sections.Add("---")
        $sections.Add("")
        $sections.Add("## $speaker - $timestamp")
        $sections.Add("")
        $sections.Add($text)
        $messageCount++
    }
}
finally {
    $reader.Dispose()
    $fileStream.Dispose()
}

[System.IO.File]::WriteAllText(
    $outputFullPath,
    (($sections -join "`r`n") + "`r`n"),
    $utf8NoBom
)

Write-Output "Esportati $messageCount messaggi in: $outputFullPath"
