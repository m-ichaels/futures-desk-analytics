# Build the C++ replay extension in place with MSVC:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
$vs = Get-ChildItem "C:\Program Files*\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $vs) { throw "vcvars64.bat not found - install Visual Studio Build Tools (C++ workload)" }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
cmd /c "`"$($vs.FullName)`" >nul 2>&1 && set DISTUTILS_USE_SDK=1&& set MSSdk=1&& cd /d `"$root`" && python build_ext.py build_ext --inplace --force"
exit $LASTEXITCODE
