param([switch]$Hapus)

$proyek = Split-Path -Parent $MyInvocation.MyCommand.Path
$namaTugas = "Solo OSINT Rutinitas"

if ($Hapus) {
    $ada = Get-ScheduledTask -TaskName $namaTugas -ErrorAction SilentlyContinue
    if ($ada) {
        Unregister-ScheduledTask -TaskName $namaTugas -Confirm:$false
        Write-Host "Tugas dihapus: $namaTugas"
    } else {
        Write-Host "Tugas tidak ditemukan: $namaTugas"
    }
    return
}

$python = (Get-Command python -ErrorAction Stop).Source
$argumen = '-m osint.rutinitas --seeds "data\seeds.txt" --check-alive'
$aksi = New-ScheduledTaskAction -Execute $python -Argument $argumen -WorkingDirectory $proyek

$trigger = @()
foreach ($jam in @("07:00", "19:00")) {
    $trigger += New-ScheduledTaskTrigger -Daily -At $jam
}

$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$setting = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask `
    -TaskName $namaTugas `
    -Action $aksi `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $setting `
    -Description "Rutinitas OSINT pasif solo: panen crt.sh, impor aman, cek hidup, backup JSON. Waktu mengikuti jam lokal komputer (set WIB)." `
    -Force | Out-Null

Write-Host "Tugas terdaftar: $namaTugas (07.00 dan 19.00 waktu lokal)"
Write-Host "Jalankan sekarang: $proyek\jalankan-rutinitas-sekali.bat"
