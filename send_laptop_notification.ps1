param(
    [string]$Title = "⚡ Unstop 30-Day Challenge Alert!",
    [string]$Body = "Hey Animesh! Today's Unstop Challenge is waiting for you! Keep your streak alive! 🚀",
    [string]$Link = "https://unstop.com/practice/coding",
    [string]$SubText = "Click to open in Unstop.",
    [string]$Tag = "unstop_single_toast",
    [string]$Group = "unstop"
)

$toastShown = $false

# ─── 1. Attempt Modern Windows WinRT Toast Notification ─────────────────────────
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $shortcutPath = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Unstop Challenge Tracker.lnk"
    $appIds = @(
        "{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe",
        "Microsoft.Windows.ShellExperienceHost",
        $shortcutPath
    )

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

    foreach ($candidateAppId in $appIds) {
        try {
            [Windows.UI.Notifications.ToastNotificationManager]::History.Clear($candidateAppId)
            [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($candidateAppId).Show($toast)
            $toastShown = $true
            break
        } catch {}
    }
} catch {}

# ─── 2. Bulletproof Fallback: Windows Forms NotifyIcon Balloon Tip ─────────────
# Ensures notification ALWAYS appears on screen even if Windows Focus Assist or Toast service is muted
if (-not $toastShown) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        Add-Type -AssemblyName System.Drawing

        $notify = New-Object System.Windows.Forms.NotifyIcon
        $notify.Icon = [System.Drawing.SystemIcons]::Information
        $notify.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
        $notify.BalloonTipTitle = $Title
        $notify.BalloonTipText = "$Body`n$SubText"
        $notify.Visible = $true

        # Click event opens challenge link directly in default browser
        $notify.add_BalloonTipClicked({
            try { [System.Diagnostics.Process]::Start($Link) } catch {}
        })

        $notify.ShowBalloonTip(7000)
        Start-Sleep -Milliseconds 2500
        $notify.Dispose()
    } catch {}
}
