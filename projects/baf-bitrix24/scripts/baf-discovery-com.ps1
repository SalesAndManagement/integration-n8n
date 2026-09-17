<#
.SYNOPSIS
    BAF: розвідка метаданих через COM-конектор 1С. ТІЛЬКИ ЧИТАННЯ.

.DESCRIPTION
    Підключається до файлової бази 1С через V83.COMConnector, читає СТРУКТУРУ
    конфігурації (імена довідників, регістрів та їхніх полів) і пише JSON.

    Що скрипт НЕ робить:
      - не виконує жодного запиту до даних;
      - нічого не записує, не змінює і не видаляє в базі;
      - не змінює конфігурацію;
      - не встановлює монопольний режим.
    Читаються тільки метадані, які вже є в пам'яті сеансу.

    Попередні умови:
      1. Зареєстрований COM-конектор (одноразово, від адміністратора):
         regsvr32 "C:\Program Files\BAF\8.3.19.1529\bin\comcntr.dll"
      2. 64-бітний PowerShell (платформа BAF 64-бітна).
      3. Вільна ліцензія 1С — COM-сеанс займає одну на час роботи.

.EXAMPLE
    .\baf-discovery-com.ps1 -Base "C:\bas\bases\sh_demo" -User "Админ"

.EXAMPLE
    .\baf-discovery-com.ps1 -Base "C:\bas\bases\sh_demo" -User "Админ" -OutFile "$env:USERPROFILE\baf_metadata.json"
#>

[CmdletBinding()]
param(
    # Шлях до бази. За замовчуванням ДЕМО — робочу вказувати свідомо.
    [string] $Base = 'C:\bas\bases\sh_demo',

    [string] $User = 'Админ',

    # Пароль запитується інтерактивно, щоб не залишати його в history.
    [System.Security.SecureString] $Password,

    [string] $OutFile = (Join-Path $env:TEMP 'baf_metadata.json')
)

$ErrorActionPreference = 'Stop'

function Test-Bitness {
    if (-not [Environment]::Is64BitProcess) {
        throw "PowerShell запущений у 32-бітному режимі, а платформа BAF 64-бітна. Відкрийте звичайний (64-бітний) PowerShell."
    }
}

function Get-ComCollectionItems {
    param($Collection)
    $items = @()
    if ($null -eq $Collection) { return $items }
    $count = $Collection.Количество()
    for ($i = 0; $i -lt $count; $i++) {
        $items += $Collection.Получить($i)
    }
    return $items
}

function Get-FieldDescriptions {
    param($Collection)
    $result = @()
    foreach ($field in (Get-ComCollectionItems $Collection)) {
        $typeText = ''
        try { $typeText = [string] $field.Тип } catch { $typeText = '' }
        $result += [ordered] @{
            name    = [string] $field.Имя
            synonym = [string] $field.Синоним
            type    = $typeText
        }
    }
    return $result
}

function Test-NameMatches {
    param([string] $Name, [string[]] $Masks)
    $lower = $Name.ToLower()
    foreach ($mask in $Masks) {
        if ($lower.Contains($mask.ToLower())) { return $true }
    }
    return $false
}

Test-Bitness

if (-not (Test-Path (Join-Path $Base '1Cv8.1CD'))) {
    throw "Не знайдено файлову базу: $Base (очікується 1Cv8.1CD у цій теці)"
}

if (-not $Password) {
    $Password = Read-Host -Prompt "Пароль користувача 1С '$User' (Enter, якщо порожній)" -AsSecureString
}
$plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Password))

Write-Host "База (тільки читання): $Base"
Write-Host "Користувач 1С: $User"

$connector = $null
$connection = $null

try {
    try {
        $connector = New-Object -ComObject V83.COMConnector
    } catch {
        throw @"
COM-конектор не зареєстрований. Потрібна одна команда від адміністратора:
    regsvr32 "C:\Program Files\BAF\8.3.19.1529\bin\comcntr.dll"
Вона нічого не змінює ні в базі, ні в конфігурації.
Оригінальна помилка: $($_.Exception.Message)
"@
    }

    $connectionString = 'File="{0}";Usr="{1}";Pwd="{2}";' -f $Base, $User, $plainPassword
    $connection = $connector.Connect($connectionString)

    $metadata = $connection.Метаданные

    $catalogMasks = @('номенклат','характер','единиц','упаковк','склад','цен','штрихкод','серии','групп','вид')
    $accumMasks   = @('остат','запас','товар','резерв','склад','свободн')
    $infoMasks    = @('цен','штрихкод','номенклат','единиц')

    $catalogs = @()
    foreach ($catalog in (Get-ComCollectionItems $metadata.Справочники)) {
        if (-not (Test-NameMatches $catalog.Имя $catalogMasks)) { continue }
        $catalogs += [ordered] @{
            name               = [string] $catalog.Имя
            synonym            = [string] $catalog.Синоним
            hierarchical       = [bool]   $catalog.Иерархический
            standardAttributes = Get-FieldDescriptions $catalog.СтандартныеРеквизиты
            attributes         = Get-FieldDescriptions $catalog.Реквизиты
        }
    }

    function Get-RegisterDescriptions {
        param($Collection, [string[]] $Masks)
        $result = @()
        foreach ($register in (Get-ComCollectionItems $Collection)) {
            if (-not (Test-NameMatches $register.Имя $Masks)) { continue }
            $result += [ordered] @{
                name       = [string] $register.Имя
                synonym    = [string] $register.Синоним
                dimensions = Get-FieldDescriptions $register.Измерения
                resources  = Get-FieldDescriptions $register.Ресурсы
                attributes = Get-FieldDescriptions $register.Реквизиты
            }
        }
        return $result
    }

    $report = [ordered] @{
        collectedAt            = (Get-Date).ToString('s')
        base                   = $Base
        configurationName      = [string] $metadata.Имя
        configurationSynonym   = [string] $metadata.Синоним
        configurationVersion   = [string] $metadata.Версия
        catalogs               = $catalogs
        accumulationRegisters  = Get-RegisterDescriptions $metadata.РегистрыНакопления $accumMasks
        informationRegisters   = Get-RegisterDescriptions $metadata.РегистрыСведений $infoMasks
        allCatalogNames        = (Get-ComCollectionItems $metadata.Справочники        | ForEach-Object { [string] $_.Имя })
        allAccumRegisterNames  = (Get-ComCollectionItems $metadata.РегистрыНакопления | ForEach-Object { [string] $_.Имя })
        allInfoRegisterNames   = (Get-ComCollectionItems $metadata.РегистрыСведений   | ForEach-Object { [string] $_.Имя })
    }

    $report | ConvertTo-Json -Depth 8 | Set-Content -Path $OutFile -Encoding UTF8

    Write-Host ""
    Write-Host "Готово. Файл: $OutFile"
    Write-Host ("Довідників детально: {0}, регістрів накопичення: {1}, регістрів відомостей: {2}" -f `
        $catalogs.Count, $report.accumulationRegisters.Count, $report.informationRegisters.Count)
    Write-Host "У файлі тільки імена полів конфігурації — залишків, цін і контрагентів там немає."
}
finally {
    # Звільняємо COM-об'єкти, щоб не тримати ліцензію 1С.
    foreach ($comObject in @($connection, $connector)) {
        if ($comObject) {
            try { [void][Runtime.InteropServices.Marshal]::ReleaseComObject($comObject) } catch { }
        }
    }
    $plainPassword = $null
    [GC]::Collect()
}
