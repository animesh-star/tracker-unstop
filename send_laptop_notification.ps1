$ErrorActionPreference = "Stop"

[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

$xmlString = @"
<toast launch="https://unstop.com/practice/coding" activationType="protocol">
    <visual>
        <binding template="ToastGeneric">
            <text>⚡ Unstop 30-Day Challenge Alert!</text>
            <text>Hey Animesh! Day 1 Unstop Challenge is waiting for you! Keep your streak alive! 🚀</text>
            <text>Click to open your daily challenge.</text>
        </binding>
    </visual>
    <actions>
        <action content="Open Challenge" arguments="https://unstop.com/practice/coding" activationType="protocol" />
    </actions>
</toast>
"@

$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDoc.LoadXml($xmlString)

$appId = "Unstop Challenge Tracker"
$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDoc)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)

Write-Host "✅ Notification delivered to laptop screen!"
