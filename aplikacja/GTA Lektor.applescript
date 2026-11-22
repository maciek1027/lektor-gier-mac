-- Aplikacja „GTA Lektor”: uruchamia serwer panelu w oknie Terminala (jeśli nie działa) i otwiera panel w osobnym oknie Chrome.
-- Serwer NIE może być dzieckiem tej aplikacji: macOS zabija procesy apletu, gdy ten kończy pracę.
-- __KATALOG__ podmienia aplikacja/zbuduj.sh na folder, w którym leży repo (nie przenoś go potem bez ponownej budowy).
set startCmd to "__KATALOG__/aplikacja/start-panel.command"
set panelUrl to "http://127.0.0.1:8797/"
set sprawdz to "curl -sf -o /dev/null --max-time 1 " & panelUrl & "api/stan"
try
	do shell script sprawdz
on error
	do shell script "open -a Terminal " & quoted form of startCmd
	set gotowy to false
	repeat 40 times
		delay 0.5
		try
			do shell script sprawdz
			set gotowy to true
			exit repeat
		end try
	end repeat
	if not gotowy then
		display dialog "Panel lektora nie wystartował." & return & return & "Zajrzyj do okna Terminala „GTA Lektor” albo do ~/Library/Logs/gta-lektor.log" buttons {"OK"} default button 1 with icon caution
		return
	end if
end try
do shell script "open -na 'Google Chrome' --args --app=" & panelUrl & " --window-size=800,1000"
