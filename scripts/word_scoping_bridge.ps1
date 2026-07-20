param(
    [Parameter(Mandatory = $true)]
    [string]$CommandName,

    [Parameter(Mandatory = $true)]
    [string]$PayloadPath
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Runtime.InteropServices

function Write-DebugLog {
    param([string]$Message)
    [Console]::Error.WriteLine("[word_scoping_bridge.ps1] $Message")
}

function Read-Payload {
    if (-not (Test-Path -LiteralPath $PayloadPath)) {
        throw "Payload file not found: $PayloadPath"
    }
    $raw = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8
    if ([string]::IsNullOrWhiteSpace($raw)) {
        return @{}
    }
    $object = $raw | ConvertFrom-Json
    $result = @{}
    foreach ($property in $object.PSObject.Properties) {
        $result[$property.Name] = $property.Value
    }
    return $result
}

function Get-FormFieldTypeName {
    param($FormField)

    switch ([int]$FormField.Type) {
        70 { return "text" }
        71 { return "checkbox" }
        83 { return "dropdown" }
        default { return "unknown" }
    }
}

function Get-FormFieldValue {
    param($FormField)

    $typeName = Get-FormFieldTypeName -FormField $FormField
    switch ($typeName) {
        "checkbox" { return [bool]$FormField.CheckBox.Value }
        default { return [string]$FormField.Result }
    }
}

function Get-DropdownChoices {
    param($FormField)

    if ((Get-FormFieldTypeName -FormField $FormField) -ne "dropdown") {
        return @()
    }
    $choices = New-Object System.Collections.Generic.List[string]
    for ($index = 1; $index -le [int]$FormField.DropDown.ListEntries.Count; $index++) {
        $choices.Add([string]$FormField.DropDown.ListEntries.Item($index).Name)
    }
    return @($choices)
}

function Get-FormFieldDescriptor {
    param($FormField)

    return [ordered]@{
        index   = [int]$FormField.Index
        name    = [string]$FormField.Name
        type    = Get-FormFieldTypeName -FormField $FormField
        value   = Get-FormFieldValue -FormField $FormField
        choices = @(Get-DropdownChoices -FormField $FormField)
    }
}

function Assert-TemplateLayout {
    param(
        $Document,
        [hashtable]$Payload
    )

    $actualFieldCount = [int]$Document.FormFields.Count
    if ($Payload.ContainsKey("expectedFieldCount")) {
        $expectedFieldCount = [int]$Payload.expectedFieldCount
        if ($actualFieldCount -ne $expectedFieldCount) {
            throw "Template field count changed: expected $expectedFieldCount, found $actualFieldCount."
        }
    }

    $actualCounts = @{text = 0; checkbox = 0; dropdown = 0; unknown = 0}
    for ($index = 1; $index -le $actualFieldCount; $index++) {
        $field = $Document.FormFields.Item($index)
        $typeName = Get-FormFieldTypeName -FormField $field
        $actualCounts[$typeName] = [int]$actualCounts[$typeName] + 1
    }

    if ($Payload.ContainsKey("expectedTypeCounts")) {
        foreach ($typeName in @("text", "checkbox", "dropdown")) {
            $expectedCount = [int]$Payload.expectedTypeCounts.$typeName
            $actualCount = [int]$actualCounts[$typeName]
            if ($actualCount -ne $expectedCount) {
                throw "Template $typeName field count changed: expected $expectedCount, found $actualCount."
            }
        }
    }

    return [ordered]@{
        fieldCount = $actualFieldCount
        typeCounts = $actualCounts
    }
}

function Reset-FormFields {
    param($Document)

    for ($index = 1; $index -le [int]$Document.FormFields.Count; $index++) {
        $field = $Document.FormFields.Item($index)
        switch (Get-FormFieldTypeName -FormField $field) {
            "text" { $field.Result = "" }
            "checkbox" { $field.CheckBox.Value = $false }
            "dropdown" {
                if ([int]$field.DropDown.ListEntries.Count -gt 0) {
                    $field.DropDown.Value = 1
                }
            }
        }
    }
}

function Set-FormFieldValue {
    param(
        $Document,
        $FieldValue
    )

    $index = [int]$FieldValue.index
    if ($index -lt 1 -or $index -gt [int]$Document.FormFields.Count) {
        throw "Mapped Word field index is out of range: $index."
    }

    $field = $Document.FormFields.Item($index)
    $actualType = Get-FormFieldTypeName -FormField $field
    $expectedType = [string]$FieldValue.type
    if ($actualType -ne $expectedType) {
        throw "Mapped field '$($FieldValue.id)' expected type $expectedType at index $index, found $actualType."
    }

    switch ($actualType) {
        "text" {
            $field.Result = [string]$FieldValue.value
        }
        "checkbox" {
            $field.CheckBox.Value = [bool]$FieldValue.value
        }
        "dropdown" {
            $requestedValue = [string]$FieldValue.value
            $matchedIndex = 0
            for ($choiceIndex = 1; $choiceIndex -le [int]$field.DropDown.ListEntries.Count; $choiceIndex++) {
                $choice = [string]$field.DropDown.ListEntries.Item($choiceIndex).Name
                if ($choice -ieq $requestedValue) {
                    $matchedIndex = $choiceIndex
                    break
                }
            }
            if ($matchedIndex -eq 0) {
                $choices = (Get-DropdownChoices -FormField $field) -join ", "
                throw "Invalid dropdown value '$requestedValue' for '$($FieldValue.id)'. Allowed values: $choices"
            }
            $field.DropDown.Value = $matchedIndex
        }
    }
}

function Close-WordDocument {
    param($Document)

    if ($null -eq $Document) {
        return
    }
    try {
        $Document.Close(0)
    } finally {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($Document)
    }
}

function Close-WordApplication {
    param($WordApp)

    if ($null -eq $WordApp) {
        return
    }
    try {
        $WordApp.Quit()
    } finally {
        [void][System.Runtime.InteropServices.Marshal]::FinalReleaseComObject($WordApp)
    }
}

function Invoke-WithWordDocument {
    param(
        [string]$TemplatePath,
        [scriptblock]$Action,
        $ActionContext
    )

    if ([string]::IsNullOrWhiteSpace($TemplatePath)) {
        throw "Missing templatePath."
    }
    if (-not (Test-Path -LiteralPath $TemplatePath -PathType Leaf)) {
        throw "Word template not found: $TemplatePath"
    }

    $word = $null
    $document = $null
    try {
        Write-DebugLog "Opening Word template read-only: $TemplatePath"
        $word = New-Object -ComObject Word.Application
        $word.Visible = $false
        $word.DisplayAlerts = 0
        $document = $word.Documents.Open($TemplatePath, $false, $true)
        return & $Action $word $document $ActionContext
    } finally {
        Close-WordDocument -Document $document
        Close-WordApplication -WordApp $word
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
        [GC]::Collect()
        [GC]::WaitForPendingFinalizers()
    }
}

function Inspect-Template {
    param([hashtable]$Payload)

    return Invoke-WithWordDocument -TemplatePath ([string]$Payload.templatePath) -ActionContext $Payload -Action {
        param($WordApp, $Document, $Context)

        $layout = Assert-TemplateLayout -Document $Document -Payload $Context
        $fields = New-Object System.Collections.Generic.List[object]
        for ($index = 1; $index -le [int]$Document.FormFields.Count; $index++) {
            $fields.Add((Get-FormFieldDescriptor -FormField $Document.FormFields.Item($index)))
        }
        return [ordered]@{
            templatePath = [string]$Context.templatePath
            fieldCount   = $layout.fieldCount
            typeCounts   = $layout.typeCounts
            fields       = @($fields)
        }
    }
}

function Fill-Template {
    param([hashtable]$Payload)

    $outputPath = [string]$Payload.outputPath
    if ([string]::IsNullOrWhiteSpace($outputPath)) {
        throw "Missing outputPath."
    }
    if ([System.IO.Path]::GetExtension($outputPath) -ine ".docx") {
        throw "Generated scoping documents must use the .docx extension."
    }
    if (Test-Path -LiteralPath $outputPath) {
        throw "Refusing to overwrite generated document: $outputPath"
    }

    return Invoke-WithWordDocument -TemplatePath ([string]$Payload.templatePath) -ActionContext $Payload -Action {
        param($WordApp, $Document, $Context)

        $destination = [string]$Context.outputPath
        $layout = Assert-TemplateLayout -Document $Document -Payload $Context
        Write-DebugLog "Saving isolated DOCX copy: $destination"
        $Document.SaveAs2($destination, 16)
        Reset-FormFields -Document $Document

        $filledCount = 0
        foreach ($fieldValue in @($Context.values)) {
            Set-FormFieldValue -Document $Document -FieldValue $fieldValue
            $filledCount++
        }
        $Document.Save()

        return [ordered]@{
            outputPath  = $destination
            fieldCount  = $layout.fieldCount
            filledCount = $filledCount
        }
    }
}

$payload = Read-Payload
switch ($CommandName) {
    "inspect_template" {
        (Inspect-Template -Payload $payload) | ConvertTo-Json -Depth 10 -Compress
    }
    "fill_template" {
        (Fill-Template -Payload $payload) | ConvertTo-Json -Depth 10 -Compress
    }
    default {
        throw "Unsupported CommandName: $CommandName"
    }
}
