@echo off
setlocal

:: This is a mock docker executable to simulate the Docker CLI in an environment without Docker.
:: It parses the requested constraints and simulates the behavior of the payload.

echo [Docker Mock] Received command: %*

:: Check for memory flag
echo %* | findstr /I "\-m 512m" >nul
if errorlevel 1 (
    echo [Docker Mock] FATAL: Missing memory limit.
    exit /b 1
)

:: Check for network flag
echo %* | findstr /I "\-\-network none" >nul
if errorlevel 1 (
    echo [Docker Mock] FATAL: Missing network isolation.
    exit /b 1
)

echo [Docker Mock] Sandboxing verified: Memory capped to 512m, Network = none

:: Simulate the malicious payload attempting outbound network
echo [Sandbox] Attempting outbound curl to http://malicious.com...
echo [Sandbox] curl: (6) Could not resolve host: malicious.com

:: Simulate the malicious payload attempting memory exhaustion
echo [Sandbox] Allocating memory...
echo [Sandbox] MemoryError: Memory allocation failed, hit cgroup limit 512M
echo [Sandbox] Killed

exit /b 137

