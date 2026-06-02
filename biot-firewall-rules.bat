@echo off
netsh advfirewall firewall delete rule name="BioT FastAPI 8001 ANY" >nul 2>&1
netsh advfirewall firewall delete rule name="BioT MQTT 1883 ANY" >nul 2>&1
netsh advfirewall firewall add rule name="BioT FastAPI 8001 ANY" dir=in action=allow protocol=TCP localport=8001 profile=any
netsh advfirewall firewall add rule name="BioT MQTT 1883 ANY" dir=in action=allow protocol=TCP localport=1883 profile=any
