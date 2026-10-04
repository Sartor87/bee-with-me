@rem B60: record the size of the file at call time (restricting an EMPTY file means no key was ever readable)
@for %%F in (%1) do @echo icacls size=%%~zF>>"%BWM_STUB_LOG%"
@if "%BWM_STUB_ICACLS_FAIL%"=="1" exit /b 5
@"%SystemRoot%\System32\icacls.exe" %*
