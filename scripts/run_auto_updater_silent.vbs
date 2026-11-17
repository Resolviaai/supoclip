Set WshShell = CreateObject("WScript.Shell")
Set FSO = CreateObject("Scripting.FileSystemObject")
ScriptDir = FSO.GetParentFolderName(WScript.ScriptFullName)
BatPath = ScriptDir & "\run_auto_updater.bat"
' Run with window style 0 (completely hidden) and wait for completion
WshShell.Run """" & BatPath & """", 0, True
