<#
Regenerates stereo_speech.wav, the fixture for the real-model split-mode smoke test.

  powershell -ExecutionPolicy Bypass -File packages/engine/tests/fixtures/make_stereo_speech.ps1

Needs Windows (System.Speech / SAPI) and Python with numpy. It synthesises two neutral phrases
as 16 kHz mono 16-bit WAVs, then combine_stereo.py joins them into one stereo file:
  LEFT  (channel 0): "The weather today is clear and bright", from 0.5 s.
  RIGHT (channel 1): "Please send the quarterly report by Friday", after LEFT ends plus 0.5 s.
Each side is silent while the other speaks. Leading and trailing synthesiser silence is trimmed to keep the file small.
#>
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$tmp = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $tmp | Out-Null
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
    16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
    [System.Speech.AudioFormat.AudioChannel]::Mono)
$phrases = @{
    left  = 'The weather today is clear and bright'
    right = 'Please send the quarterly report by Friday'
}
foreach ($side in $phrases.Keys) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    $synth.SetOutputToWaveFile((Join-Path $tmp "$side.wav"), $format)
    $synth.Speak($phrases[$side])
    $synth.Dispose()
}
python (Join-Path $here 'combine_stereo.py') (Join-Path $tmp 'left.wav') (Join-Path $tmp 'right.wav') (Join-Path $here 'stereo_speech.wav')
if ($LASTEXITCODE -ne 0) { throw 'combine_stereo.py failed' }
Remove-Item -Recurse -Force $tmp
