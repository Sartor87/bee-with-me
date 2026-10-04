@if "%BWM_STUB_ICACLS_FAIL%"=="1" exit /b 5
@"%SystemRoot%\System32\icacls.exe" %*
