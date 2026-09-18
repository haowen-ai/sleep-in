import AppKit
import ServiceManagement
import Carbon

struct TerminationPolicy {
    private var draining = false
    private var completed = false
    var decision: String { completed ? "exit" : draining ? "wait" : "ask" }
    mutating func begin() { draining=true }
    mutating func failed() { draining=false; completed=false }
    mutating func stopped() { draining=false; completed=true }
}

// This preview is unsigned/ad-hoc signed. Distribution signing/notarization is a release gate.
final class SleepInDelegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var statusItem: NSMenuItem!
    var loginItem: NSMenuItem!
    var busy = false
    var termination = TerminationPolicy()
    var knownEnabled = false
    var retryCount = 0
    var nextRetry = Date.distantPast
    var timer: Timer?
    let defaults = UserDefaults.standard
    let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Sleep In")
    var launcher: URL { Bundle.main.resourceURL!.appendingPathComponent("app/launch-mac.command") }
    var detailLog: URL { root.appendingPathComponent("native-launch.txt") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "Sleep In"
        let menu = NSMenu()
        statusItem = menu.addItem(withTitle: "Starting…", action: nil, keyEquivalent: "")
        menu.addItem(.separator())
        add(menu,"Open Sleep In",#selector(openApp))
        add(menu,"Start background service",#selector(startService))
        add(menu,"Stop background service…",#selector(stopService))
        loginItem = menu.addItem(withTitle:"Start at Login…",action:#selector(toggleLogin),keyEquivalent:"")
        loginItem.target = self
        add(menu,"View details",#selector(viewDetails))
        menu.addItem(.separator())
        add(menu,"Quit completely…",#selector(quitCompletely))
        item.menu = menu
        refreshRegistration()
        let login = NSAppleEventManager.shared().currentAppleEvent?.paramDescriptor(forKeyword:keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        launch(login ? "--login" : "--launch", promptRegistration: !login)
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.refresh() }
    }
    func add(_ menu:NSMenu,_ title:String,_ action:Selector) { let row=menu.addItem(withTitle:title,action:action,keyEquivalent:""); row.target=self }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool { openApp(); return false }

    func command(_ argument:String,capture:Bool=false)->(Int32,String) {
        let process=Process(); process.executableURL=URL(fileURLWithPath:"/bin/bash"); process.arguments=[launcher.path,argument]
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        let pipe=Pipe()
        if capture { process.standardOutput=pipe; process.standardError=FileHandle.nullDevice }
        else {
            if !FileManager.default.fileExists(atPath:detailLog.path) { FileManager.default.createFile(atPath:detailLog.path,contents:nil) }
            let handle=try? FileHandle(forWritingTo:detailLog); _ = try? handle?.seekToEnd()
            process.standardOutput=handle; process.standardError=handle
        }
        do { try process.run(); process.waitUntilExit() }
        catch { return (1,error.localizedDescription) }
        let text=capture ? String(data:pipe.fileHandleForReading.readDataToEndOfFile(),encoding:.utf8) ?? "" : ""
        return (process.terminationStatus,text)
    }
    func launch(_ argument:String,promptRegistration:Bool=false,automatic:Bool=false) {
        guard !busy else {return}; if !automatic { retryCount=0 }; busy=true; statusItem.title="Preparing background service…"
        DispatchQueue.global().async {
            let result=self.command(argument)
            DispatchQueue.main.async {
                self.busy=false
                if result.0 != 0 {
                    self.statusItem.title="Background needs attention"
                    if automatic { self.retryCount += 1; self.nextRetry=Date().addingTimeInterval(min(60,pow(2,Double(self.retryCount))*5)); return }
                    let alert=NSAlert(); alert.messageText="Sleep In could not become ready"
                    alert.informativeText="Choose View details for installation or component errors. Retry preserves your existing account and workflows."
                    alert.addButton(withTitle:"Retry"); alert.addButton(withTitle:"View details"); alert.addButton(withTitle:"Close")
                    let choice=alert.runModal()
                    if choice == .alertFirstButtonReturn { self.launch(argument,promptRegistration:promptRegistration) }
                    else if choice == .alertSecondButtonReturn { self.viewDetails() }
                } else {
                    self.refresh()
                    if promptRegistration && !self.defaults.bool(forKey:"loginChoiceShown") { self.offerRegistration() }
                }
            }
        }
    }
    func refreshRegistration() {
        let value=SMAppService.mainApp.status
        loginItem.state=value == .enabled ? .on : .off
        loginItem.title=value == .requiresApproval ? "Login startup: approval needed…" : "Start at Login…"
        knownEnabled=value == .enabled
    }
    func offerRegistration() {
        defaults.set(true,forKey:"loginChoiceShown")
        let alert=NSAlert(); alert.messageText="Keep Sleep In ready after login?"
        alert.informativeText="Background mode keeps your Mac awake on AC and battery while allowing the screen to lock and turn off. It uses battery between tasks. Start at Login restores the service after you sign in; explicit Stop stays stopped. Lid closure, system Sleep, shutdown and critical battery can interrupt schedules."
        alert.addButton(withTitle:"Enable Start at Login"); alert.addButton(withTitle:"Not now")
        if alert.runModal() == .alertFirstButtonReturn { register() }
    }
    func register() {
        do { try SMAppService.mainApp.register(); refreshRegistration(); if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() } }
        catch { showError("Login startup could not be enabled",error.localizedDescription) }
    }
    @objc func toggleLogin() {
        if SMAppService.mainApp.status == .enabled {
            let alert=NSAlert(); alert.messageText="Disable login startup and stop background service?"
            alert.informativeText="Scheduled tasks pause. Active work can finish before the background service stops."
            alert.addButton(withTitle:"Disable and finish active work"); alert.addButton(withTitle:"Cancel")
            if alert.runModal() == .alertFirstButtonReturn {
                do { try SMAppService.mainApp.unregister(); launch("--stop-finish"); refreshRegistration() }
                catch { showError("Could not disable login startup",error.localizedDescription) }
            }
        } else if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() }
        else { offerRegistration() }
    }
    @objc func refresh() {
        guard !busy else {return}
        let wasEnabled=knownEnabled
        refreshRegistration()
        if wasEnabled && SMAppService.mainApp.status != .enabled { launch("--stop-cancel"); return }
        DispatchQueue.global().async {
            let result=self.command("--status",capture:true)
            let value=(try? JSONSerialization.jsonObject(with:Data(result.1.utf8))) as? [String:Any] ?? [:]
            DispatchQueue.main.async {
                let state=value["state"] as? String ?? "unavailable"
                self.statusItem.title=state == "running" ? "Background running" : "Background: \(state)"
                // Native companion restores after supervisor crashes, respecting explicit Stop.
                if state == "running" { self.retryCount=0 }
                if state == "stopped" && value["running_preference"] as? Bool == true && self.retryCount<5 && Date()>=self.nextRetry { self.launch("--login",automatic:true) }
            }
        }
    }
    @objc func openApp() { launch("--launch") }
    @objc func startService() { launch("--start") }
    func chooseStop()->String? {
        let alert=NSAlert(); alert.messageText="Stop background service?"
        alert.informativeText="Future schedules will pause and idle-sleep protection will end. If work is active, choose whether to finish or cancel it. Sleep In reports draining until workers actually stop."
        alert.addButton(withTitle:"Finish active work"); alert.addButton(withTitle:"Cancel active work"); alert.addButton(withTitle:"Keep running")
        switch alert.runModal() { case .alertFirstButtonReturn:return "--stop-finish"; case .alertSecondButtonReturn:return "--stop-cancel"; default:return nil }
    }
    @objc func stopService() { if let action=chooseStop() { launch(action) } }
    @objc func quitCompletely() { NSApp.terminate(nil) }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        switch termination.decision {
        case "exit": return .terminateNow
        case "wait": return .terminateLater
        default: break
        }
        guard !busy else {
            showError("Background action still in progress", "Wait for the current start or stop operation, then choose Quit again.")
            return .terminateCancel
        }
        guard let action=chooseStop() else { return .terminateCancel }
        termination.begin(); busy=true; statusItem.title="Draining before quit…"
        DispatchQueue.global().async {
            let result=self.command(action)
            guard result.0 == 0 else {
                DispatchQueue.main.async {
                    self.termination.failed(); self.busy=false
                    sender.reply(toApplicationShouldTerminate:false)
                    self.showError("Could not stop", "View details, then retry. The companion has stayed open.")
                }
                return
            }
            while true {
                let response=self.command("--status",capture:true)
                let value=(try? JSONSerialization.jsonObject(with:Data(response.1.utf8))) as? [String:Any]
                if ["stopped","not_installed"].contains(value?["state"] as? String ?? "") {break}
                if response.0 != 0 || value == nil {
                    DispatchQueue.main.async {
                        self.termination.failed(); self.busy=false
                        sender.reply(toApplicationShouldTerminate:false)
                        self.showError("Stop could not be verified", "The companion has stayed open. View details before retrying Quit.")
                    }
                    return
                }
                Thread.sleep(forTimeInterval:1)
            }
            DispatchQueue.main.async {
                self.termination.stopped()
                sender.reply(toApplicationShouldTerminate:true)
            }
        }
        return .terminateLater
    }
    @objc func viewDetails() { NSWorkspace.shared.open(detailLog) }
    func showError(_ title:String,_ text:String) { let alert=NSAlert(); alert.messageText=title; alert.informativeText=text; alert.runModal() }
}

if CommandLine.arguments.contains("--self-test") {
    let launcher=Bundle.main.resourceURL?.appendingPathComponent("app/launch-mac.command")
    guard let launcher=launcher, FileManager.default.isExecutableFile(atPath:launcher.path) else { fputs("Missing packaged launcher\n",stderr); exit(1) }
    var policy=TerminationPolicy()
    var decisions=[policy.decision]
    policy.begin(); decisions.append(policy.decision)
    policy.failed(); decisions.append(policy.decision)
    policy.begin(); decisions.append(policy.decision)
    policy.stopped(); decisions.append(policy.decision)
    let evidence:[String:Any] = ["packaged_resources_ready":true,"termination_decisions":decisions,"side_effects_performed":false]
    let data=try! JSONSerialization.data(withJSONObject:evidence,options:[.sortedKeys])
    print(String(data:data,encoding:.utf8)!)
    exit(0)
}
let application=NSApplication.shared
let delegate=SleepInDelegate()
application.delegate=delegate
application.run()
