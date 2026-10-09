# READ-ONLY inventory. No repair, rename, deletion, or copying.
$stage = 'C:\EvolvePakTools\CustomSigning\staged'
$root = 'C:\EvolvePakTools\CustomSigning\UniversalPakManager'
$paths = @(
    "$stage\paks\Game\libs.pak",
    "$stage\paks\Game\UI_Data.pak",
    "$stage\mykeys\public_key.bin",
    "$stage\rekey_plan.json",
    "$root\Projects\Game_libs",
    'C:\Games\ModdedEvolve\EvolveGame\Game\libs.pak',
    'C:\EvolvePakTools\PauseSwapTest\controlled_swap_state.json'
)
foreach ($p in $paths) {
    if (Test-Path -LiteralPath $p) {
        $item = Get-Item -LiteralPath $p
        $size = if ($item.PSIsContainer) { '(directory)' } else { "$($item.Length) bytes" }
        Write-Host "PRESENT  $size  $p"
    } else { Write-Host "MISSING  $p" }
}
Write-Host "`nThis check does not change any files. If libs.pak is MISSING, do not run Extract again."
