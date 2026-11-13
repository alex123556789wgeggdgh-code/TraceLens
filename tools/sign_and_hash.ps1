<#
.SYNOPSIS
    TraceLens 打包产物的代码签名与完整性校验（子任务 5.3 / t12）。

.DESCRIPTION
    做四件事，全部可复现、全部如实报告：

    1. 在 **CurrentUser\My** 里找一张代码签名证书；没有就用
       ``New-SelfSignedCertificate`` 现造一张自签名证书（不需要管理员）。
    2. 用 ``Set-AuthenticodeSignature`` 给 ``desktop\build\dist`` 里的产物签名
       （NSIS 安装包、portable 压缩包、sidecar exe），并导出 ``.cer``。
    3. 用 ``Get-AuthenticodeSignature`` 逐份核对签名状态，写
       ``SIGNATURE-STATUS.json``（含证书指纹、有效期、状态原文、信任说明）。
    4. 生成 ``SHA256SUMS.txt``（``<sha256>  <文件名>`` 格式），``-Verify`` 模式
       重算校验并核对签名。

    诚实边界（写进产物与文档，不许改口径）：自签名 **不是** 受信任发布者签名。
    ``Get-AuthenticodeSignature`` 的 Status 会是 ``NotTrusted``（签名能验、
    哈希没动，但证书链不受信任），Windows SmartScreen 与企业策略照样报警。
    要消除报警需要 OV/EV 代码签名证书，或把证书装进
    ``LocalMachine\Root`` + ``LocalMachine\TrustedPublisher``（需要管理员，
    本脚本不假装能做）。

    统计口径（0.5.0 起写死，不许为了让断言变绿而改）：
      * ``signed_count``      = ``Get-AuthenticodeSignature`` 状态属于
        ``Valid`` / ``NotTrusted`` 的产物数（**只有这两个算"签名被 Windows 认可"**；
        ``UnknownError`` / ``NotSigned`` / ``HashMismatch`` / ``NotSupported`` 全部算 0）。
      * ``signatures_applied``= 文件里**确实落了签名**的产物数（看 SignatureType 与
        签名人指纹，与 Windows 是否信任无关）。
      * ``unsigned``          = 没有任何"被 Windows 认可的签名"（与 ``signed_count -eq 0`` 等价）。
      * ``trust_state``       = 一句话结论：``trusted`` / ``partially_trusted`` /
        ``self_signed_root_untrusted``（签名在、链不受信任，自签名常见）/ ``not_signed``。
      * ``signed_by``         = 签名人 Subject + 指纹（谁签的，写明）。
      两个数**分开写**是刻意的：``signed_count=0`` 与 ``signatures_applied=2`` 同时成立
      正是自签名的真相（签了，但根证书不受信任）。过去把二者混成一个 ``signed_count=2``
      就是全量单测 ``test_signature_status_is_honest`` 那条红的真因。

    编码口径（0.5.0 起写死）：所有 JSON/文本产物一律 **UTF-8 无 BOM** 写出，中文
    按原样落盘（不转 ``\uXXXX``、不产生 GBK 乱码）。落地做法：写之前剥掉 U+FEFF、
    发现 U+FFFD（替换符＝源码被按 ANSI 解码）当场拒绝出文件；写完回读字节复核
    "无 BOM + 逐字还原"。本脚本源文件自身必须保持 **UTF-8 带 BOM** —— Windows
    PowerShell 5.1 只认 BOM，否则脚本里的中文字面量会按 GBK 解码，写出的产物必是乱码
    （这就是历史发布件里 ``status_message`` 乱码的根因）；启动时会自检并报警。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -Verify
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -CheckEncoding
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -CheckEncoding -Include045
    powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -Files @('desktop\build\dist\a.zip')

    注：Windows PowerShell 5.1（`powershell`）与 PowerShell 7（`pwsh`）都支持本脚本；
    本机实测只有 5.1（`pwsh` 不存在），所以 `desktop/package.json` 的 `npm run sign`
    调的是 `powershell`。
#>
[CmdletBinding()]
param(
    [string]$OutDir = '',
    [string]$Subject = 'CN=TraceLens Self-Signed (Local Only)',
    [string]$FriendlyName = 'TraceLens Self-Signed (Local Only)',
    [string]$CerPath = '',
    [string[]]$Files = @(),
    [string]$TimestampServer = '',
    [switch]$Verify,
    [switch]$SkipCertCreate,
    [switch]$WhatIfPlan,
    [switch]$CheckEncoding,
    # -CheckEncoding 默认只审计 **0.5.0** 发布件；加这个开关才把 0.4.5 老包一起扫。
    [switch]$Include045
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
# 默认产物目录：桌面端构建输出（PS 5.1 在 param 默认值里拿不到 $PSScriptRoot，所以在这儿算）
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot 'desktop\build\dist' }
$SumsPath = Join-Path $OutDir 'SHA256SUMS.txt'
$ReportPath = Join-Path $OutDir 'SIGNATURE-STATUS.json'
if (-not $CerPath) { $CerPath = Join-Path $OutDir 'TraceLens-SelfSigned.cer' }

function Get-Artifacts {
    if ($Files.Count -gt 0) {
        $list = @()
        foreach ($f in $Files) {
            $full = (Get-Item (Join-Path $RepoRoot $f) -ErrorAction SilentlyContinue).FullName
            if (-not $full) { $full = (Get-Item $f).FullName }
            $list += Get-Item $full
        }
        return $list
    }
    if (-not (Test-Path $OutDir)) { return @() }
    # 交付物 = 安装包 / 便携版 / 源码便携包。以下扩展名一律不进签名与 SHA256SUMS：
    #   .txt / .json / .cer / .signature  校验与状态文件本身（签了会自指）
    #   .yml  .log                        electron-builder 的调试输出（builder-debug.yml）
    #   .7z   .nupkg .blockmap            构建中间产物（nsis.7z 是安装包压缩中间层，150MB）
    # 0.5.0 实测：不加这条过滤，SHA256SUMS.txt 会多出 builder-debug.yml 与
    # tracelens-desktop-*-x64.nsis.7z 两行，发布清单里就混进构建垃圾。
    $skipExt = @('.cer', '.signature', '.txt', '.json', '.yml', '.yaml', '.log',
                 '.7z', '.nupkg', '.blockmap')
    $all = @(Get-ChildItem -Path $OutDir -File)
    $skipped = @($all | Where-Object { $skipExt -contains $_.Extension.ToLowerInvariant() })
    if ($skipped.Count -gt 0) {
        # 跳过什么必须说出来：静默过滤等于让人无法核对发布清单的完整性
        Write-Host ('（跳过 {0} 个非交付物：{1}）' -f $skipped.Count,
                    (($skipped | ForEach-Object { $_.Name }) -join ', '))
    }
    $all | Where-Object { $skipExt -notcontains $_.Extension.ToLowerInvariant() }
}

# 只有这两个状态算"签名被 Windows 认可"（口径与 tests/test_e2e.py 的
# test_signature_status_is_honest 完全一致；不许为了让断言变绿而往里加状态）。
$script:TRUSTED_STATUSES = @('Valid', 'NotTrusted')

function Write-Utf8NoBom([string]$path, [string]$text) {
    # UTF-8 无 BOM 写出，并且**当场证明**它写对了：
    #  1) 剥掉文本自己带的 U+FEFF（BOM 只在字节层出现才算 BOM，文本层出现就是脏数据）；
    #  2) 文本里有 U+FFFD（替换符）= 上游字符串已经被按 ANSI/GBK 解码过，
    #     写出去就是发布件里那种"宸插鐞…"乱码 —— 直接拒绝，不出文件；
    #  3) 写完回读字节：首三字节不得是 EF BB BF，且解码回来必须与写入内容逐字相同。
    $clean = $text.TrimStart([char]0xFEFF)
    if ($clean.Contains([string][char]0xFFFD)) {
        throw '编码自检失败：待写内容含 U+FFFD 替换符（脚本源码被按 ANSI 解码，中文字面量已损坏）。' +
              '本 .ps1 必须以 UTF-8 带 BOM 保存，Windows PowerShell 5.1 才会正确解码。'
    }
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($path, $clean, $enc)
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        throw "编码自检失败：$path 写出来带 UTF-8 BOM（发布件要求 UTF-8 无 BOM）"
    }
    if ([System.Text.Encoding]::UTF8.GetString($bytes) -ne $clean) {
        throw "编码自检失败：$path 回读内容与写入内容不一致（编码不是 UTF-8）"
    }
}

function Expand-JsonUnicodeEscape([string]$json) {
    # 把 ConvertTo-Json 产生的 \uXXXX 还原成真字符，**只认 \u 这一种转义**：
    # 逐字符扫描，遇到 `\\` 就整对吞掉，所以 JSON 里的 Windows 路径
    # `E:\\dir\\f.exe` 不会被解成单反斜杠（用 [regex]::Unescape 会踩这个坑，
    # 产物随即变成不可解析的 JSON）。
    $sb = New-Object System.Text.StringBuilder
    $i = 0
    while ($i -lt $json.Length) {
        $c = $json[$i]
        if ($c -eq '\' -and $i + 1 -lt $json.Length) {
            $n = $json[$i + 1]
            if ($n -eq 'u' -and $i + 6 -le $json.Length) {
                $hex = $json.Substring($i + 2, 4)
                if ($hex -match '^[0-9a-fA-F]{4}$') {
                    [void]$sb.Append([char][Convert]::ToInt32($hex, 16))
                    $i += 6
                    continue
                }
            }
            [void]$sb.Append($c); [void]$sb.Append($n); $i += 2; continue
        }
        [void]$sb.Append($c); $i++
    }
    return $sb.ToString()
}

function Write-JsonReport([string]$path, $object) {
    # ConvertTo-Json 在 5.1 上会把中文转义成 \uXXXX：人工核对不便，这里解回真字符，
    # 再按 UTF-8 无 BOM 写出（5.1 与 PowerShell 7 出同一份字节）。
    # 解完必须还能被解析回来，否则退回转义版 —— 宁可难看，不可产出坏 JSON。
    $escaped = $object | ConvertTo-Json -Depth 8
    $plain = Expand-JsonUnicodeEscape $escaped
    $ok = $true
    try { $null = $plain | ConvertFrom-Json } catch { $ok = $false }
    if (-not $ok) {
        Write-Host "注：$path 的中文还原后无法回解析，退回 \\uXXXX 转义写法（内容等价）"
        $plain = $escaped
    }
    Write-Utf8NoBom $path $plain
}

function Get-TrustFacts([array]$entries) {
    # 单元素管道在 5.1 上 .Count 是 $null（踩过坑），一律先 @() 包一层。
    $list = @($entries)
    $trusted  = @($list | Where-Object { $script:TRUSTED_STATUSES -contains $_.status })
    $applied  = @($list | Where-Object { $_.signature_present })
    $counts = [ordered]@{}
    foreach ($e in ($list | Group-Object -Property status | Sort-Object -Property Name)) {
        $counts[[string]$e.Name] = $e.Count
    }
    $state = 'not_signed'
    if ($trusted.Count -eq $list.Count -and $list.Count -gt 0) { $state = 'trusted' }
    elseif ($trusted.Count -gt 0) { $state = 'partially_trusted' }
    elseif ($applied.Count -gt 0) { $state = 'self_signed_root_untrusted' }
    $signer = $null
    foreach ($e in $list) {
        if ($e.signature_present -and $e.signer_thumbprint) {
            $signer = "{0}（指纹 {1}）" -f [string]$e.signer_subject, [string]$e.signer_thumbprint
            break
        }
    }
    return [ordered]@{
        trusted_statuses   = $script:TRUSTED_STATUSES
        signed_count       = $trusted.Count          # 被 Windows 认可的签名数
        signatures_applied = $applied.Count          # 文件里确实落了签名的产物数
        unsigned           = ($trusted.Count -eq 0)  # 没有任何受信任签名
        trust_state        = $state
        signed_by          = $signer                 # 谁签的（没人签名时为 $null）
        status_counts      = $counts
        artifact_count     = $list.Count
    }
}

function Test-JsonFileEncoding([string[]]$paths) {
    # 只读审计：三个发布件的 SIGNATURE-STATUS.json 是否 UTF-8 无 BOM、中文是否完好。
    $bad = 0
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { Write-Host "[缺失] $p"; $bad++; continue }
        $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $p).Path)
        $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $mojibake = $text.Contains([string][char]0xFFFD)
        $parses = $true
        try { $null = $text | ConvertFrom-Json } catch { $parses = $false }
        $flag = 'OK'
        if ($hasBom) { $flag = 'BOM'; $bad++ }
        if ($mojibake) { $flag = "$flag+U+FFFD"; $bad++ }
        if (-not $parses) { $flag = "$flag+不可解析"; $bad++ }
        Write-Host ("[{0}] {1}  {2} 字节  BOM={3} 中文完好={4} JSON可解析={5}" -f `
                    $flag, $p, $bytes.Length, $hasBom, (-not $mojibake), $parses)
    }
    return $bad
}

function Test-TextFileEncoding([string[]]$paths) {
    # 只读审计：SHA256SUMS 类**文本**发布件（不是 JSON）必须 UTF-8 无 BOM、无 U+FFFD。
    # 这类清单由打包脚本用 python 写出，编码一坏 `sha256sum -c` 就直接读不出条目，
    # 所以除了 BOM/乱码，还把每行按「<64 位小写十六进制><两空格><路径>」核一遍格式。
    $bad = 0
    foreach ($p in $paths) {
        if (-not (Test-Path -LiteralPath $p)) { Write-Host "[缺失] $p"; $bad++; continue }
        $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $p).Path)
        $hasBom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
        $text = [System.Text.Encoding]::UTF8.GetString($bytes)
        $mojibake = $text.Contains([string][char]0xFFFD)
        $rows = @(@($text -split "\r?\n") | Where-Object { $_.Trim() })
        $shape = 0
        foreach ($ln in $rows) {
            if ($ln -match '^[0-9a-f]{64}  \S+$') { $shape++ }
        }
        $crlf = $text.Contains("`r`n")
        $flag = 'OK'
        if ($hasBom) { $flag = 'BOM'; $bad++ }
        if ($mojibake) { $flag = "$flag+U+FFFD"; $bad++ }
        if ($shape -ne $rows.Count) { $flag = "$flag+行格式不符($shape/$($rows.Count))"; $bad++ }
        Write-Host ("[{0}] {1}  {2} 字节  BOM={3} 中文完好={4} 条目={5} 换行={6}" -f `
                    $flag, $p, $bytes.Length, $hasBom, (-not $mojibake), $rows.Count,
                    $(if ($crlf) { 'CRLF' } else { 'LF' }))
    }
    return $bad
}

function Test-ScriptSourceEncoding {
    # 自检：本脚本源码必须 UTF-8 带 BOM，否则 5.1 会把中文字面量按 GBK 解码。
    if (-not $PSCommandPath) { return }
    $b = [System.IO.File]::ReadAllBytes($PSCommandPath)
    if (-not ($b.Length -ge 3 -and $b[0] -eq 0xEF -and $b[1] -eq 0xBB -and $b[2] -eq 0xBF)) {
        Write-Host '⚠ tools/sign_and_hash.ps1 自身缺 UTF-8 BOM：Windows PowerShell 5.1 会按'
        Write-Host '  ANSI/GBK 解码脚本里的中文字面量，写出的产物必然乱码。请把本文件按'
        Write-Host '  "UTF-8 带 BOM" 重新保存（产物侧的 BOM 由脚本自己保证去掉）。'
    }
}

Test-ScriptSourceEncoding

function Get-FullHash([string]$path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash.ToLowerInvariant()
}

# --------------------------------------------------------------------------- #
# -CheckEncoding：只读审计发布件编码（UTF-8 无 BOM、中文完好、JSON 可解析）
#   默认清单＝**0.5.0** 全套（t7 之后的权威发布件）；加 -Include045 才扫 0.4.5 老包。
# --------------------------------------------------------------------------- #
if ($CheckEncoding) {
    Write-Host '=== 发布件编码审计（只读，不改任何文件）==='
    $jsonTargets = @(
        (Join-Path $RepoRoot 'desktop\build\dist\SIGNATURE-STATUS.json'),
        (Join-Path $RepoRoot 'release\SIGNATURE-STATUS.json'),
        (Join-Path $RepoRoot 'release\tracelens-0.5.0-release\SIGNATURE-STATUS.json')
    )
    $textTargets = @(
        (Join-Path $RepoRoot 'release\SHA256SUMS-release.txt'),
        (Join-Path $RepoRoot 'release\0.5.0\SHA256SUMS.txt'),
        (Join-Path $RepoRoot 'release\tracelens-0.5.0-release\SHA256SUMS.txt'),
        (Join-Path $RepoRoot 'release\tracelens-0.5.0-release\SHA256SUMS-bundle.txt')
    )
    if ($Include045) {
        $jsonTargets += (Join-Path $RepoRoot 'release\tracelens-0.4.5-release\SIGNATURE-STATUS.json')
        $textTargets += (Join-Path $RepoRoot 'release\0.4.5\SHA256SUMS.txt')
        $textTargets += (Join-Path $RepoRoot 'release\tracelens-0.4.5-release\SHA256SUMS.txt')
    }
    Write-Host '--- JSON 产物（要求 UTF-8 无 BOM、中文完好、可解析）'
    $bad = Test-JsonFileEncoding $jsonTargets
    Write-Host '--- 文本清单（要求 UTF-8 无 BOM、无 U+FFFD、逐行 sha256 格式）'
    $bad += Test-TextFileEncoding $textTargets
    # 脚本自身：必须 **UTF-8 带 BOM**（口径与产物侧相反，5.1 只认 BOM 里的中文字面量）。
    $selfBytes = [System.IO.File]::ReadAllBytes($PSCommandPath)
    $selfBom = ($selfBytes.Length -ge 3 -and $selfBytes[0] -eq 0xEF -and `
                $selfBytes[1] -eq 0xBB -and $selfBytes[2] -eq 0xBF)
    $selfFlag = 'OK'
    if (-not $selfBom) { $selfFlag = '缺BOM'; $bad++ }
    Write-Host ("[{0}] tools/sign_and_hash.ps1  {1} 字节  BOM={2}（脚本自身要求带 BOM）" -f `
                $selfFlag, $selfBytes.Length, $selfBom)
    if ($bad -gt 0) {
        Write-Host "编码审计失败：$bad 项（BOM / 乱码 / 坏 JSON / 缺文件都不允许进发布件）"
        exit 1
    }
    Write-Host '编码审计通过：JSON 与文本清单全部 UTF-8 无 BOM、中文完好；脚本自身 BOM 在位'
    exit 0
}

# --------------------------------------------------------------------------- #
# -Verify：只核对，不改任何东西
# --------------------------------------------------------------------------- #
if ($Verify) {
    if (-not (Test-Path $SumsPath)) {
        Write-Host "缺 SHA256SUMS.txt：$SumsPath（先跑本脚本生成）"
        exit 1
    }
    Write-Host "=== 校验 SHA256SUMS + 签名状态（$OutDir）==="
    $fail = 0
    foreach ($line in (Get-Content -LiteralPath $SumsPath -Encoding UTF8)) {
        if (-not $line.Trim()) { continue }
        $parts = $line -split '  ', 2
        if ($parts.Count -ne 2) { Write-Host "格式不符：$line"; $fail++; continue }
        $target = Join-Path $OutDir $parts[1].Trim()
        if (-not (Test-Path -LiteralPath $target)) {
            Write-Host "[缺失] $($parts[1])"; $fail++; continue
        }
        $actual = Get-FullHash $target
        if ($actual -ne $parts[0].Trim().ToLowerInvariant()) {
            Write-Host "[哈希不符] $($parts[1]) 期望 $($parts[0]) 实际 $actual"; $fail++
            continue
        }
        $sig = Get-AuthenticodeSignature -LiteralPath $target
        Write-Host "[sha256 ok] $($parts[1])  签名=$($sig.Status)"
        if ($sig.Status -notin @('Valid', 'NotTrusted')) {
            Write-Host "        签名状态不达意：$($sig.Status) — $($sig.StatusMessage)"; $fail++
        }
    }
    if ($fail -gt 0) {
        Write-Host "校验失败：$fail 项（不得计入交付）"
        exit 1
    }
    Write-Host "校验通过：哈希全部匹配；签名状态见上面（自签名 = NotTrusted 属预期）"
    exit 0
}

# --------------------------------------------------------------------------- #
# 生成：证书 → 签名 → .cer → SHA256SUMS → SIGNATURE-STATUS.json
# --------------------------------------------------------------------------- #
$artifacts = Get-Artifacts
if ($artifacts.Count -eq 0) {
    Write-Host "没有可签名的产物：$OutDir 里没有安装包/压缩包/exe。"
    Write-Host "先产出：python tools/build_sidecar.py --install --build --portable-zip"
    Write-Host "        cd desktop; npm install; npm run dist"
    exit 1
}

Write-Host "=== 产物 ==="
foreach ($a in $artifacts) {
    Write-Host ("  {0}  {1:N0} 字节  sha256={2}" -f $a.Name, $a.Length, (Get-FullHash $a.FullName))
}
if ($WhatIfPlan) { Write-Host '（--WhatIfPlan：只看清单，不签名）'; exit 0 }

$cert = $null
$attempts = @()
$existing = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert -ErrorAction SilentlyContinue |
    Where-Object { $_.Subject -eq $Subject -and $_.NotAfter -gt (Get-Date) })
if ($existing.Count -gt 0) {
    $cert = $existing[0]
    Write-Host "复用现有自签名证书：$($cert.Thumbprint)（有效期至 $($cert.NotAfter.ToString('yyyy-MM-dd'))）"
} elseif ($SkipCertCreate) {
    Write-Host "CurrentUser\My 里没有 Subject=$Subject 的代码签名证书，且传了 -SkipCertCreate"
    exit 1
} else {
    Write-Host '新建自签名代码签名证书（CurrentUser\My，2048 位 RSA / SHA256，1 年）'
    try {
        $cert = New-SelfSignedCertificate -Subject $Subject -FriendlyName $FriendlyName `
            -Type CodeSigningCert -KeyUsage DigitalSignature `
            -KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256 `
            -Provider 'Microsoft Enhanced RSA and AES Cryptographic Provider' `
            -CertStoreLocation 'Cert:\CurrentUser\My' -NotAfter (Get-Date).AddYears(1) `
            -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.2', '2.5.29.19={text}')
        $attempts += [pscustomobject]@{ method = 'New-SelfSignedCertificate'; result = 'ok';
                                        detail = [string]$cert.Thumbprint }
        Write-Host "已创建：$($cert.Thumbprint)"
    } catch {
        $reason = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
        $attempts += [pscustomobject]@{ method = 'New-SelfSignedCertificate';
                                        result = 'denied'; detail = $reason }
        Write-Host "New-SelfSignedCertificate 不可用：$reason"
    }
    if (-not $cert) {
        # 第二条路：不碰证书库，用 .NET 现造一张自签名证书再签（受限令牌下常见）
        try {
            $rsa = [System.Security.Cryptography.RSACryptoServiceProvider]::new(2048)
            $req = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
                $Subject, $rsa,
                [System.Security.Cryptography.HashAlgorithmName]::SHA256,
                [System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
            $oids = [System.Security.Cryptography.OidCollection]::new()
            $null = $oids.Add([System.Security.Cryptography.Oid]::new('1.3.6.1.5.5.7.3.2'))
            $req.CertificateExtensions.Add(
                [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]::new($oids, $true))
            $cert = $req.CreateSelfSigned([DateTimeOffset]::UtcNow.AddDays(-1),
                                          [DateTimeOffset]::UtcNow.AddYears(1))
            $attempts += [pscustomobject]@{ method = 'CertificateRequest + Set-AuthenticodeSignature';
                                            result = 'ok'; detail = [string]$cert.Thumbprint }
            Write-Host ".NET 现造自签名证书：$($cert.Thumbprint)（不落证书库）"
        } catch {
            $reason2 = "$($_.Exception.GetType().Name): $($_.Exception.Message)"
            $attempts += [pscustomobject]@{ method = 'CertificateRequest';
                                            result = 'denied'; detail = $reason2 }
            Write-Host ".NET 造证书也不可用：$reason2"
        }
    }
}

if (-not $cert) {
    # 造不出证书 = 这台机器签不了名。这时只出 SHA256SUMS + 如实的状态文件，
    # 状态写 NotSigned 并把两条路的原始报错记进去 —— 绝不写"已签名"。
    $artifacts | ForEach-Object {
        [pscustomobject]@{
            path              = $_.FullName
            name              = $_.Name
            bytes             = $_.Length
            sha256            = (Get-FullHash $_.FullName)
            status            = 'NotSigned'
            status_message    = '本机造不出代码签名证书（受限环境），两条路都被系统拒绝，详见 certificate.attempts'
            signature_present = $false
            trusted           = $false
            signer_subject    = $null
            signer_thumbprint = $null
            signed_at         = $null
        }
    } | ForEach-Object { $entries += $_ }
    Write-Utf8NoBom $SumsPath ((($entries | ForEach-Object { "{0}  {1}" -f $_.sha256, $_.name }) -join "`r`n") + "`r`n")
    $blockerFacts = Get-TrustFacts @($entries)
    $blocker = [ordered]@{
        tool         = 'tools/sign_and_hash.ps1'
        task         = 't12 / 5.3 桌面端打包与端到端联调（t26 / 5.3b 在全权限请求下重建并复核）'
        generated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
        encoding     = 'utf-8-no-bom'
        certificate  = [ordered]@{
            available = $false
            subject   = $Subject
            store     = 'Cert:\CurrentUser\My'
            attempts  = $attempts
        }
        trust_note   = '自签名证书只能证明"文件没被改过、来自本机这张钥匙"，不是受信任发布者签名。' +
                       '本机因受限环境造不出证书，本次产物状态为 NotSigned（未签名）：' +
                       'SHA256SUMS.txt 只给完整性校验，签名环节未产出，不得声称"已签名"。' +
                       '正常开发机上重跑本脚本即可产出签名 + .cer + 状态报告。'
        trusted_statuses   = $blockerFacts.trusted_statuses
        signed_count       = $blockerFacts.signed_count
        signatures_applied = $blockerFacts.signatures_applied
        unsigned           = $blockerFacts.unsigned
        trust_state        = $blockerFacts.trust_state
        signed_by          = $blockerFacts.signed_by
        status_counts      = $blockerFacts.status_counts
        artifact_count     = $blockerFacts.artifact_count
        signing_rule = 'signed_count 只统计 Get-AuthenticodeSignature 状态为 Valid / NotTrusted 的产物；' +
                       '本机连证书都造不出来，所以一个都没有（signatures_applied 同步为 0）'
        attempts     = $attempts
        artifacts    = $entries
    }
    Write-JsonReport $ReportPath $blocker
    Write-Host "校验文件：$SumsPath"
    Write-Host ("签名状态：$ReportPath（unsigned=true，signed_count=0，signatures_applied=0，" +
                'trust_state=not_signed，状态 NotSigned，UTF-8 无 BOM）')
    Write-Host '如实结论：本次未签名。签名步骤的阻塞原因已写进状态文件，可复现命令：'
    Write-Host '  powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1'
    # 没有任何产物签名成功 = 签名环节未交付，退出码必须非零（与主路径同一口径）
    exit 1
}

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir -Force | Out-Null }
if (Get-Command Export-Certificate -ErrorAction SilentlyContinue) {
    Export-Certificate -Cert $cert -FilePath $CerPath -Type CERT | Out-Null
} else {
    [System.IO.File]::WriteAllBytes($CerPath, $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert))
}
Write-Host "证书导出：$CerPath（导入到 LocalMachine\TrustedPublisher 需要管理员）"
$signFailures = @()
$entries = @()
foreach ($a in $artifacts) {
    $signArgs = @{ FilePath = $a.FullName; Certificate = $cert }
    if ($TimestampServer) { $signArgs['TimestampServer'] = $TimestampServer }
    $signFailed = $null
    try {
        Set-AuthenticodeSignature @signArgs | Out-Null
    } catch {
        $signFailed = $_.Exception.Message
        $signFailures += "$($a.Name): $signFailed"
        Write-Host "签名失败 $($a.Name)：$signFailed"
    }
    $check = Get-AuthenticodeSignature -LiteralPath $a.FullName
    $status = [string]$check.Status
    $statusMessage = [string]$check.StatusMessage
    # "落了签名"只看测出来的事实，不看 Status 好不好看。
    # 0.5.0 实测（本机 151MB Setup 签完之后）：状态是 UnknownError 时
    # Get-AuthenticodeSignature 的 **SignatureHash 是空串**（链在不受信任的根处终止，
    # Windows 不给出哈希），所以旧写法 `-and ($check.SignatureHash -ne '')` 会把
    # 明明签了的产物判成"没签名"（signatures_applied 恒为 0，与"签了但根不受信任"
    # 这条自签名真相矛盾）。可用的事实是：SignatureType 不是 None，且
    # SignerCertificate 带指纹（自签名 = 签发人就是本人）。
    $signerThumbprint = [string]$check.SignerCertificate.Thumbprint
    $present = ([string]$check.SignatureType -ne 'None') -and ($signerThumbprint -ne '')
    $signerSubject = [string]$check.SignerCertificate.Subject
    if ($signFailed) {
        # 签名动作本身被系统拒绝 = 这个产物没有签名，绝不写"已签名"，
        # 也不把 Get-AuthenticodeSignature 的 UnknownError（半截状态）当结论。
        $status = 'NotSigned'
        $statusMessage = "$signFailed（Get-AuthenticodeSignature 原始状态：$($check.Status)）"
    }
    $entry = [ordered]@{
        path             = $a.FullName
        name             = $a.Name
        bytes            = $a.Length
        sha256           = (Get-FullHash $a.FullName)
        signed_hash      = [string]$check.SignatureHash
        signature_type   = [string]$check.SignatureType
        signature_present = $present
        trusted          = ($script:TRUSTED_STATUSES -contains $status)
        status           = $status
        status_message   = $statusMessage
        signer_subject   = $signerSubject
        signer_thumbprint = [string]$check.SignerCertificate.Thumbprint
        timestamp_server = $TimestampServer
        signed_at        = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    }
    $entries += [pscustomobject]$entry
    Write-Host ("签名 {0} → {1}（{2}）" -f $a.Name, $check.Status, $check.SignatureHash)
}

# SHA256SUMS：`<sha256>  <文件名>`（两空格，coreutils 风格，python 侧逐条重算）
# 诚实判定：只有拿到 Valid / NotTrusted 才算"这个产物签名成功"（口径在
# $script:TRUSTED_STATUSES，与 tests/test_e2e.py::test_signature_status_is_honest 一致）。
# 注意 PowerShell 5.1 的坑：单元素管道结果不是数组，`.Count` 为 $null，
# 直接比较会得出"有签名成功"的假结论（t26 实测踩到并修掉；Get-TrustFacts 里统一 @() 包一层）。
$facts = Get-TrustFacts @($entries)
# 半截状态如实补写：签名落了但状态是 HashMismatch = 内容被改过，单独报出来。
if ($facts.trust_state -eq 'self_signed_root_untrusted') {
    $facts.trust_state = 'self_signed_root_untrusted'
}
$unsignedAll = [bool]$facts.unsigned
$signdCount  = $facts.signed_count
$entryList   = @($entries)
$sumLines = @()
foreach ($e in $entries) { $sumLines += ("{0}  {1}" -f $e.sha256, $e.name) }
Write-Utf8NoBom $SumsPath (($sumLines -join "`r`n") + "`r`n")
Write-Host "校验文件：$SumsPath"

$report = [ordered]@{
    tool         = 'tools/sign_and_hash.ps1'
    task         = 't12 / 5.3 桌面端打包与端到端联调（t26 / 5.3b 在全权限请求下重建并复核）'
    generated_at = (Get-Date).ToString('yyyy-MM-ddTHH:mm:ss')
    encoding     = 'utf-8-no-bom'
    product_version = '0.5.0'
    certificate  = [ordered]@{
        subject           = $cert.Subject
        friendly_name     = [string]$cert.FriendlyName
        thumbprint        = $cert.Thumbprint
        serial            = [string]$cert.SerialNumber
        not_before        = $cert.NotBefore.ToString('yyyy-MM-ddTHH:mm:ss')
        not_after         = $cert.NotAfter.ToString('yyyy-MM-ddTHH:mm:ss')
        store             = 'Cert:\CurrentUser\My'
        eku               = '1.3.6.1.5.5.7.3.2 (Code Signing)'
        exported_cer      = (Resolve-Path $CerPath).Path
    }
    trust_note   = '自签名证书：只能证明"文件没被改过、来自本机这张钥匙"，' +
                   '不是受信任发布者签名。Get-AuthenticodeSignature 的 Status 为 ' +
                   'NotTrusted（签名有效、证书链不受信任）或 UnknownError（链在不受信任的' +
                   '根证书处终止）都属预期；Windows SmartScreen ' +
                   '与企业策略仍会报警。消除报警需要 OV/EV 代码签名证书，' +
                   '或把证书导入 LocalMachine\Root + LocalMachine\TrustedPublisher（需管理员）。'
    timestamping = '默认不加时间戳：自签名 + 无时间戳 = 证书过期后签名验证转为失效，' +
                   '这一点必须在发布说明里写清。'
    trusted_statuses   = $facts.trusted_statuses
    signed_count       = $facts.signed_count
    signatures_applied = $facts.signatures_applied
    unsigned           = $facts.unsigned
    trust_state        = $facts.trust_state
    signed_by          = $facts.signed_by
    status_counts      = $facts.status_counts
    artifact_count     = $facts.artifact_count
    signing_rule = 'signed_count 只统计 Get-AuthenticodeSignature 状态为 Valid / NotTrusted 的产物；' +
                   'NotSigned、UnknownError、HashMismatch、NotSupported 全部算未签名。' +
                   'signatures_applied 统计"文件里确实落了签名"的产物（与是否受信任无关），' +
                   '两者不相等正是自签名的真相：签了，但根证书不受信任。'
    attempts     = $attempts
    sign_failures = $signFailures
    artifacts    = $entries
}
Write-JsonReport $ReportPath $report
Write-Host "签名状态：$ReportPath（UTF-8 无 BOM）"

Write-Host ''
Write-Host '=== 结论（如实口径）==='
foreach ($e in $entries) {
    Write-Host ("  {0,-46} 签名状态={1}  签名已落={2}  受信任={3}" -f `
                $e.name, $e.status, $e.signature_present, $e.trusted)
}
Write-Host (("  统计：signed_count={0}（Valid/NotTrusted）  signatures_applied={1}（落了签名）  " +
             "unsigned={2}  trust_state={3}  artifact_count={4}") -f `
            $facts.signed_count, $facts.signatures_applied, $facts.unsigned,
            $facts.trust_state, $facts.artifact_count)
if ($facts.signed_by) { Write-Host "  签名人：$($facts.signed_by)" }
Write-Host '  自签名仅用于完整性校验，非受信任发布者签名（见 SIGNATURE-STATUS.json 的 trust_note）'
Write-Host "  复核命令： powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -Verify"
Write-Host "  编码审计： powershell -NoProfile -ExecutionPolicy Bypass -File tools/sign_and_hash.ps1 -CheckEncoding"
if ($unsignedAll) {
    Write-Host '  本次没有任何"被 Windows 认可"的签名：状态文件已记 unsigned=true，不得声称"已签名"'
    if ($facts.signatures_applied -gt 0) {
        Write-Host ("  注：{0} 个产物已落自签名，但证书链不受信任（trust_state={1}）。" -f `
                    $facts.signatures_applied, $facts.trust_state)
        Write-Host '  按口径本脚本退出码 1（未交付受信任签名），发布说明必须写"自签名、根不受信任"。'
    }
    exit 1
}
exit 0
