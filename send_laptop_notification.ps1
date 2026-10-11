param(
    [string]$Title = "⚡ Unstop 30-Day Challenge Alert!",
    [string]$Body = "Hey Animesh! Day 1 Unstop Challenge is waiting for you! Keep your streak alive! 🚀",
    [string]$Link = "https://unstop.com/practice/coding",
    [string]$SubText = "Click to open in Unstop.",
    [string]$Tag = "unstop_single_toast",
    [string]$Group = "unstop"
)

$ErrorActionPreference = "SilentlyContinue"

try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
} catch {}

# Verified Windows AppUserModelId for guaranteed delivery
$appId = "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

# Clear previous notifications to avoid clutter
try {
    [Windows.UI.Notifications.ToastNotificationManager]::History.Clear($appId)
} catch {}

if ([string]::IsNullOrWhiteSpace($SubText)) {
    $SubText = "Click to open in Unstop."
}

$xmlString = @"
<toast launch="$Link" activationType="protocol">
    <visual>
        <binding template="ToastGeneric">
            <text>$Title</text>
            <text>$Body</text>
            <text>$SubText</text>
        </binding>
    </visual>
    <audio src="ms-winsoundevent:Notification.Default" loop="false" />
    <actions>
        <action content="Open Challenge" arguments="$Link" activationType="protocol" />
    </actions>
</toast>
"@

$xmlDoc = New-Object Windows.Data.Xml.Dom.XmlDocument
$xmlDoc.LoadXml($xmlString)

$toast = [Windows.UI.Notifications.ToastNotification]::new($xmlDoc)
$toast.Tag = $Tag
$toast.Group = $Group

[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show($toast)

