param(
    [Parameter(Mandatory = $true)]
    [string]$CommandName,

    [Parameter(Mandatory = $true)]
    [string]$PayloadPath
)

$ErrorActionPreference = "Stop"

function Read-Payload {
    if (-not (Test-Path -LiteralPath $PayloadPath)) {
        return @{}
    }
    $raw = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @{}
    }
    $object = $raw | ConvertFrom-Json
    if ($null -eq $object) {
        return @{}
    }

    $result = @{}
    foreach ($property in $object.PSObject.Properties) {
        $result[$property.Name] = $property.Value
    }
    return $result
}

function New-OneNoteApplication {
    return New-Object -ComObject OneNote.Application
}

function Get-HierarchyXml {
    param($OneNoteApp)

    $hierarchy = ""
    $OneNoteApp.GetHierarchy("", 3, [ref]$hierarchy)
    return [string]$hierarchy
}

function Convert-HierarchyToSections {
    param([string]$HierarchyXml)

    [xml]$doc = $HierarchyXml
    $sections = New-Object System.Collections.Generic.List[object]

    $notebooks = $doc.SelectNodes("//*[local-name()='Notebook']")
    foreach ($notebook in $notebooks) {
        $notebookId = [string]$notebook.ID
        $notebookName = [string]$notebook.name
        $sectionNodes = $notebook.SelectNodes(".//*[local-name()='Section']")
        foreach ($section in $sectionNodes) {
            $sectionId = [string]$section.ID
            $sectionName = [string]$section.name
            if ([string]::IsNullOrWhiteSpace($sectionId) -or [string]::IsNullOrWhiteSpace($sectionName)) {
                continue
            }
            $sections.Add([ordered]@{
                notebookId   = $notebookId
                notebookName = $notebookName
                sectionId    = $sectionId
                sectionName  = $sectionName
                path         = "$notebookName / $sectionName"
            })
        }
    }

    return $sections
}

function Find-SectionById {
    param(
        [string]$HierarchyXml,
        [string]$SectionId
    )

    [xml]$doc = $HierarchyXml
    $section = $doc.SelectSingleNode("//*[local-name()='Section' and @ID='$SectionId']")
    if ($null -eq $section) {
        return $null
    }

    $notebook = $section.SelectSingleNode("ancestor::*[local-name()='Notebook'][1]")
    $notebookId = ""
    $notebookName = ""
    if ($null -ne $notebook) {
        $notebookId = [string]$notebook.ID
        $notebookName = [string]$notebook.name
    }

    return [ordered]@{
        notebookId   = $notebookId
        notebookName = $notebookName
        sectionId    = [string]$section.ID
        sectionName  = [string]$section.name
        path         = "$notebookName / $([string]$section.name)"
    }
}

function New-OneNotePage {
    param(
        $OneNoteApp,
        [hashtable]$Payload
    )

    $sectionId = [string]$Payload.sectionId
    $title = [string]$Payload.title
    $pageXmlBody = [string]$Payload.pageXmlBody

    if ([string]::IsNullOrWhiteSpace($sectionId)) {
        throw "Missing sectionId for create_page."
    }
    if ([string]::IsNullOrWhiteSpace($title)) {
        throw "Missing title for create_page."
    }

    $pageId = ""
    $OneNoteApp.CreateNewPage($sectionId, [ref]$pageId, 1)

    $pageXml = @"
<?xml version="1.0" encoding="utf-8"?>
<one:Page xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote" ID="$pageId">
  <one:PageSettings RTL="false" color="automatic">
    <one:PageSize><one:Automatic/></one:PageSize>
    <one:RuleLines visible="false"/>
  </one:PageSettings>
  <one:Title style="font-family:Calibri;font-size:17.0pt" lang="en-US">
    <one:OE alignment="left">
      <one:T><![CDATA[$title]]></one:T>
    </one:OE>
  </one:Title>
  $pageXmlBody
</one:Page>
"@

    $OneNoteApp.UpdatePageContent($pageXml, [DateTime]::MinValue)

    $link = ""
    try {
        $link = [string]$OneNoteApp.GetHyperlinkToObject($pageId, "")
    } catch {
    }

    return [ordered]@{
        pageId = [string]$pageId
        link   = [string]$link
    }
}

function New-OneNoteSection {
    param(
        $OneNoteApp,
        [hashtable]$Payload
    )

    $notebookId = [string]$Payload.notebookId
    $sectionName = [string]$Payload.sectionName

    if ([string]::IsNullOrWhiteSpace($notebookId)) {
        throw "Missing notebookId for create_section."
    }
    if ([string]::IsNullOrWhiteSpace($sectionName)) {
        throw "Missing sectionName for create_section."
    }

    $newSectionId = ""
    $safeName = $sectionName.Trim()
    $candidatePaths = @(
        "$safeName.one",
        $safeName
    )

    foreach ($candidatePath in $candidatePaths) {
        try {
            $OneNoteApp.OpenHierarchy($candidatePath, $notebookId, [ref]$newSectionId, 3)
            if (-not [string]::IsNullOrWhiteSpace($newSectionId)) {
                break
            }
        } catch {
        }
    }

    if ([string]::IsNullOrWhiteSpace($newSectionId)) {
        throw "Unable to create section '$safeName'."
    }

    $hierarchyXml = Get-HierarchyXml -OneNoteApp $OneNoteApp
    $sectionInfo = Find-SectionById -HierarchyXml $hierarchyXml -SectionId $newSectionId
    if ($null -eq $sectionInfo) {
        return [ordered]@{
            notebookId   = $notebookId
            notebookName = ""
            sectionId    = [string]$newSectionId
            sectionName  = $safeName
            path         = $safeName
        }
    }

    return $sectionInfo
}

$payload = Read-Payload

switch ($CommandName) {
    "list_sections" {
        $app = New-OneNoteApplication
        $xml = Get-HierarchyXml -OneNoteApp $app
        @{
            sections = @(Convert-HierarchyToSections -HierarchyXml $xml)
        } | ConvertTo-Json -Depth 6 -Compress
    }
    "create_page" {
        $app = New-OneNoteApplication
        (New-OneNotePage -OneNoteApp $app -Payload $payload) | ConvertTo-Json -Compress
    }
    "create_section" {
        $app = New-OneNoteApplication
        (New-OneNoteSection -OneNoteApp $app -Payload $payload) | ConvertTo-Json -Compress
    }
    default {
        throw "Unsupported CommandName: $CommandName"
    }
}
