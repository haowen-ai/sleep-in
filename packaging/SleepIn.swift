import AppKit
import ServiceManagement
import Carbon
import UniformTypeIdentifiers

let nativeChinese: [String:String] = [
 "Starting…":"正在启动…", "Open Sleep In":"打开 Sleep In", "Start background service":"启动后台服务", "Stop background service…":"停止后台服务…", "Start at Login…":"登录时启动…", "View details":"查看详情", "Quit completely…":"彻底退出…", "Preparing background service…":"正在准备后台服务…", "Background needs attention":"后台服务需要处理", "Sleep In could not become ready":"Sleep In 尚未就绪", "Choose View details for installation or component errors. Retry preserves your existing account and workflows.":"查看详情了解安装或组件错误。重试会保留现有账户和工作流。", "Retry":"重试", "Close":"关闭", "Login startup: approval needed…":"登录启动：需要批准…", "Keep Sleep In ready after login?":"登录后保持 Sleep In 就绪？", "Background mode keeps your Mac awake on AC and battery while allowing the screen to lock and turn off. It uses battery between tasks. Start at Login restores the service after you sign in; explicit Stop stays stopped. Lid closure, system Sleep, shutdown and critical battery can interrupt schedules.":"后台模式在接电和电池供电时均保持 Mac 唤醒，屏幕仍可锁定或关闭，任务间也会消耗电量。登录启动在登录后恢复服务；明确停止后会保持停止。合盖、系统睡眠、关机和严重低电量可能中断计划。", "Enable Start at Login":"启用登录启动", "Not now":"暂不", "Disable login startup and stop background service?":"禁用登录启动并停止后台服务？", "Scheduled tasks pause. Active work can finish before the background service stops.":"定时任务将暂停，可等待正在执行的任务完成后停止后台。", "Disable and finish active work":"禁用并完成当前任务", "Cancel":"取消", "Background running":"后台运行中", "Background":"后台", "Stop background service?":"停止后台服务？", "Future schedules will pause and idle-sleep protection will end. If work is active, choose whether to finish or cancel it. Sleep In reports draining until workers actually stop.":"后续计划将暂停，防止空闲睡眠保护将结束。请选择完成或取消正在执行的任务；工作进程实际停止前显示为正在收尾。", "Finish active work":"完成当前任务", "Cancel active work":"取消当前任务", "Keep running":"继续运行", "Draining before quit…":"退出前正在收尾…", "Background action still in progress":"后台操作仍在进行", "Wait for the current start or stop operation, then choose Quit again.":"请等待当前启动或停止操作完成后再次退出。", "Could not stop":"无法停止", "View details, then retry. The companion has stayed open.":"请查看详情后重试，菜单栏应用保持打开。", "Stop could not be verified":"无法确认服务已停止", "The companion has stayed open. View details before retrying Quit.":"菜单栏应用保持打开，请查看详情后再次退出。", "Login startup could not be enabled":"无法启用登录启动", "Could not disable login startup":"无法禁用登录启动", "Install signed update…":"安装已签名更新…", "Choose a signed and notarized Sleep In.app":"选择已签名并公证的 Sleep In.app", "Install update":"安装更新", "Update":"更新", "Update complete":"更新完成", "The updated background service is ready. Reopen the companion to load new menu features.":"更新后的后台服务已就绪，重新打开菜单栏应用可加载新功能。", "Update did not complete":"更新未完成", "Review update details. If readiness failed, the previous application and data were restored; the backup is retained.":"请查看更新详情。如就绪检查失败，旧版应用与数据会恢复，备份继续保留。", "No upcoming schedules":"暂无后续计划", "Power unavailable":"电源状态未知", "Battery":"电池", "AC power":"外接电源", "Low battery":"电量偏低", "Sleep In is still running. Connect power when convenient; macOS can sleep or shut down at critical battery. No schedules were paused by this alert.":"Sleep In 继续运行。请适时接电；严重低电量时 macOS 可能睡眠或关机。本提示不会暂停计划。", "Next":"下次", "Ready":"就绪", "Checking managed runtimes":"正在检查运行环境", "Downloading":"正在下载", "Preparing Python":"正在准备 Python", "Preparing dependencies":"正在准备依赖", "Preparing n8n":"正在准备 n8n", "Managed runtimes verified":"运行环境已验证", "Installation progress":"安装进度", "Downloaded":"已下载", "Total size unavailable":"总大小暂不可用", "running":"运行中", "stopped":"已停止", "starting":"正在启动", "draining":"正在收尾", "degraded":"运行异常", "unavailable":"不可用", "not_installed":"尚未安装", "Open app":"打开应用", "Preparing services":"正在准备服务"
]
func nativeText(_ value:String)->String { UserDefaults.standard.string(forKey:"locale") == "zh-CN" ? nativeChinese[value] ?? value : value }

struct TerminationPolicy {
    private var draining = false
    private var completed = false
    var decision: String { completed ? "exit" : draining ? "wait" : "ask" }
    mutating func begin() { draining=true }
    mutating func failed() { draining=false; completed=false }
    mutating func stopped() { draining=false; completed=true }
}

// This preview is unsigned/ad-hoc signed. Distribution signing/notarization is a release gate.
class SleepInDelegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem!
    var statusItem: NSMenuItem!
    var loginItem: NSMenuItem!
    var powerItem: NSMenuItem!
    var nextItem: NSMenuItem!
    var progressWindow: NSPanel?
    var progressLabel: NSTextField?
    var progressBar: NSProgressIndicator?
    var progressText: NSTextView?
    var progressTimer: Timer?
    var lowBatteryAlerted = false
    var detailsVisible = false
    var latestStatus: [String:Any] = [:]
    var busy = false
    var termination = TerminationPolicy()
    var knownEnabled = false
    var retryCount = 0
    var nextRetry = Date.distantPast
    var timer: Timer?
    let defaults = UserDefaults.standard
    var root: URL {
        if let configured=ProcessInfo.processInfo.environment["SLEEP_IN_INSTALL_DIR"],!configured.isEmpty { return URL(fileURLWithPath:configured) }
        return FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/Sleep In")
    }
    var launcher: URL { Bundle.main.resourceURL!.appendingPathComponent("app/launch-mac.command") }
    var detailLog: URL { root.appendingPathComponent("native-launch.txt") }

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "Sleep In"
        makeMenu()
        let login = NSAppleEventManager.shared().currentAppleEvent?.paramDescriptor(forKeyword:keyAEPropData)?.enumCodeValue == keyAELaunchedAsLogInItem
        launch(login ? "--login" : "--launch", promptRegistration: !login)
        timer = Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in self?.refresh() }
    }
    func makeMenu() {
        let menu = NSMenu()
        statusItem = menu.addItem(withTitle: nativeText("Starting…"), action: nil, keyEquivalent: "")
        powerItem = menu.addItem(withTitle:nativeText("Power unavailable"), action:nil, keyEquivalent:"")
        nextItem = menu.addItem(withTitle:nativeText("No upcoming schedules"), action:nil, keyEquivalent:"")
        menu.addItem(.separator())
        add(menu,"Open Sleep In",#selector(openApp))
        add(menu,"Start background service",#selector(startService))
        add(menu,"Stop background service…",#selector(stopService))
        loginItem = menu.addItem(withTitle:nativeText("Start at Login…"),action:#selector(toggleLogin),keyEquivalent:"")
        loginItem.target = self
        add(menu,"View details",#selector(viewDetails))
        add(menu,"Install signed update…",#selector(installUpdate))
        let language=NSMenuItem(title:"English / 简体中文",action:nil,keyEquivalent:"")
        let choices=NSMenu();let english=choices.addItem(withTitle:nativeText("English"),action:#selector(englishLanguage),keyEquivalent:"");english.target=self;english.state=defaults.string(forKey:"locale") != "zh-CN" ? .on : .off
        let chinese=choices.addItem(withTitle:nativeText("简体中文"),action:#selector(chineseLanguage),keyEquivalent:"");chinese.target=self;chinese.state=defaults.string(forKey:"locale") == "zh-CN" ? .on : .off;language.submenu=choices;menu.addItem(language)
        menu.addItem(.separator())
        add(menu,"Quit completely…",#selector(quitCompletely))
        item.menu = menu
        refreshRegistration()
    }
    func add(_ menu:NSMenu,_ title:String,_ action:Selector) { let row=menu.addItem(withTitle:nativeText(title),action:action,keyEquivalent:""); row.target=self }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender:NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows flag:Bool)->Bool { openApp(); return false }

    func command(_ argument:String,capture:Bool=false,extra:[String]=[])->(Int32,String) {
        let process=Process(); process.executableURL=URL(fileURLWithPath:"/bin/bash"); process.arguments=[launcher.path,argument]+extra
        try? FileManager.default.createDirectory(at:root,withIntermediateDirectories:true)
        let pipe=Pipe()
        if capture { process.standardOutput=pipe; process.standardError=FileHandle.nullDevice }
        else {
            if !FileManager.default.fileExists(atPath:detailLog.path) { FileManager.default.createFile(atPath:detailLog.path,contents:nil) }
            let handle=try? FileHandle(forWritingTo:detailLog); _ = try? handle?.seekToEnd()
            process.standardOutput=handle; process.standardError=handle
        }
        do { try process.run() }
        catch { return (1,error.localizedDescription) }
        // Drain while the child is alive: waiting first can fill the pipe and
        // deadlock status, shutdown verification or update diagnostics.
        let text=capture ? String(data:pipe.fileHandleForReading.readDataToEndOfFile(),encoding:.utf8) ?? "" : ""
        process.waitUntilExit()
        return (process.terminationStatus,text)
    }
    func launch(_ argument:String,promptRegistration:Bool=false,automatic:Bool=false) {
        guard !busy else {return}; if !automatic { retryCount=0 }; busy=true; statusItem.title=nativeText("Preparing background service…"); if !automatic { showProgress() }
        DispatchQueue.global().async {
            let result=self.command(argument)
            DispatchQueue.main.async {
                self.busy=false; self.refreshProgress()
                if result.0 != 0 {
                    self.statusItem.title=nativeText("Background needs attention")
                    if automatic { self.retryCount += 1; self.nextRetry=Date().addingTimeInterval(min(60,pow(2,Double(self.retryCount))*5)); return }
                    let alert=NSAlert(); alert.messageText=nativeText("Sleep In could not become ready")
                    alert.informativeText=nativeText("Choose View details for installation or component errors. Retry preserves your existing account and workflows.")
                    alert.addButton(withTitle:nativeText("Retry")); alert.addButton(withTitle:nativeText("View details")); alert.addButton(withTitle:nativeText("Close"))
                    let choice=self.presentAlert(alert)
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
        loginItem.title=nativeText(value == .requiresApproval ? "Login startup: approval needed…" : "Start at Login…")
        knownEnabled=value == .enabled
    }
    func offerRegistration() {
        defaults.set(true,forKey:"loginChoiceShown")
        let alert=NSAlert(); alert.messageText=nativeText("Keep Sleep In ready after login?")
        alert.informativeText=nativeText("Background mode keeps your Mac awake on AC and battery while allowing the screen to lock and turn off. It uses battery between tasks. Start at Login restores the service after you sign in; explicit Stop stays stopped. Lid closure, system Sleep, shutdown and critical battery can interrupt schedules.")
        alert.addButton(withTitle:nativeText("Enable Start at Login")); alert.addButton(withTitle:nativeText("Not now"))
        if self.presentAlert(alert) == .alertFirstButtonReturn { register() }
    }
    func register() {
        do { try SMAppService.mainApp.register(); refreshRegistration(); if SMAppService.mainApp.status == .requiresApproval { SMAppService.openSystemSettingsLoginItems() } }
        catch { showError("Login startup could not be enabled",error.localizedDescription) }
    }
    @objc func toggleLogin() {
        if SMAppService.mainApp.status == .enabled {
            let alert=NSAlert(); alert.messageText=nativeText("Disable login startup and stop background service?")
            alert.informativeText=nativeText("Scheduled tasks pause. Active work can finish before the background service stops.")
            alert.addButton(withTitle:nativeText("Disable and finish active work")); alert.addButton(withTitle:nativeText("Cancel"))
            if self.presentAlert(alert) == .alertFirstButtonReturn {
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
                self.statusItem.title=state == "running" ? nativeText("Background running") : nativeText("Background")+": "+nativeText(state)
                self.showStatus(value)
                // Native companion restores after supervisor crashes, respecting explicit Stop.
                if state == "running" { self.retryCount=0 }
                if state == "stopped" && value["running_preference"] as? Bool == true && self.retryCount<5 && Date()>=self.nextRetry { self.launch("--login",automatic:true) }
            }
        }
    }
    @objc func openApp() { launch("--launch") }
    @objc func startService() { launch("--start") }
    func chooseStop()->String? {
        let alert=NSAlert(); alert.messageText=nativeText("Stop background service?")
        alert.informativeText=nativeText("Future schedules will pause and idle-sleep protection will end. If work is active, choose whether to finish or cancel it. Sleep In reports draining until workers actually stop.")
        alert.addButton(withTitle:nativeText("Finish active work")); alert.addButton(withTitle:nativeText("Cancel active work")); alert.addButton(withTitle:nativeText("Keep running"))
        switch self.presentAlert(alert) { case .alertFirstButtonReturn:return "--stop-finish"; case .alertSecondButtonReturn:return "--stop-cancel"; default:return nil }
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
        termination.begin(); busy=true; statusItem.title=nativeText("Draining before quit…")
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
    @objc func viewDetails() { detailsVisible=true;showProgress();refreshProgress() }
    @objc func englishLanguage() { chooseLanguage("en") }
    @objc func chineseLanguage() { chooseLanguage("zh-CN") }
    func chooseLanguage(_ locale:String) {
        let wasVisible=progressWindow?.isVisible == true, previousPermission=knownEnabled
        defaults.set(locale,forKey:"locale");progressWindow?.close();progressWindow=nil;progressTimer?.invalidate();progressTimer=nil
        makeMenu();knownEnabled=previousPermission;refresh();if wasVisible {showProgress()}
    }
    func showStatus(_ value:[String:Any]) {
        latestStatus=value
        let power=value["power"] as? [String:Any] ?? [:], source=power["source"] as? String ?? "unknown"
        powerItem.title=nativeText(source == "battery" ? "Battery" : source == "ac" ? "AC power" : "Power unavailable")+((power["percent"] as? Int).map { " · \($0)%" } ?? "")
        if let next=value["next_scheduled"] as? [String:Any],let at=next["at"] as? String { let parser=ISO8601DateFormatter();parser.formatOptions=[.withInternetDateTime,.withFractionalSeconds];let date=parser.date(from:at) ?? ISO8601DateFormatter().date(from:at);let formatter=DateFormatter();formatter.locale=Locale(identifier:defaults.string(forKey:"locale") == "zh-CN" ? "zh_CN" : "en_US");formatter.dateStyle = .medium;formatter.timeStyle = .short;formatter.timeZone=TimeZone(identifier:next["timezone"] as? String ?? "UTC");nextItem.title=nativeText("Next")+": "+(next["workflow"] as? String ?? "")+" · "+(date.map {formatter.string(from:$0)} ?? at)+" "+(next["timezone"] as? String ?? "") }
        else {nextItem.title=nativeText("No upcoming schedules")}
        let low=power["low"] as? Bool == true
        if low && !lowBatteryAlerted {lowBatteryAlerted=true;showError("Low battery","Sleep In is still running. Connect power when convenient; macOS can sleep or shut down at critical battery. No schedules were paused by this alert.")}
        if !low {lowBatteryAlerted=false}
    }
    func showProgress(present:Bool=true) {
        if progressWindow == nil {
            let panel=NSPanel(contentRect:NSRect(x:0,y:0,width:580,height:360),styleMask:[.titled,.closable,.resizable],backing:.buffered,defer:false)
            panel.title="Sleep In";panel.center();panel.isReleasedWhenClosed=false
            let stack=NSStackView();stack.orientation = .vertical;stack.alignment = .leading;stack.spacing=14;stack.translatesAutoresizingMaskIntoConstraints=false
            let label=NSTextField(wrappingLabelWithString:nativeText("Preparing services"));progressLabel=label;stack.addArrangedSubview(label)
            let bar=NSProgressIndicator();bar.style = .bar;bar.isIndeterminate=true;bar.startAnimation(nil);progressBar=bar;stack.addArrangedSubview(bar)
            let scroll=NSScrollView();scroll.hasVerticalScroller=true;let text=NSTextView();text.isEditable=false;text.font=NSFont.monospacedSystemFont(ofSize:11,weight:.regular);scroll.documentView=text;progressText=text;stack.addArrangedSubview(scroll)
            let actions=NSStackView();for (name,selector) in [("Retry",#selector(startService)),("View details",#selector(viewDetails)),("Open app",#selector(openApp))] {actions.addArrangedSubview(NSButton(title:nativeText(name),target:self,action:selector))};stack.addArrangedSubview(actions)
            panel.contentView!.addSubview(stack);NSLayoutConstraint.activate([stack.leadingAnchor.constraint(equalTo:panel.contentView!.leadingAnchor,constant:22),stack.trailingAnchor.constraint(equalTo:panel.contentView!.trailingAnchor,constant:-22),stack.topAnchor.constraint(equalTo:panel.contentView!.topAnchor,constant:22),stack.bottomAnchor.constraint(equalTo:panel.contentView!.bottomAnchor,constant:-22),bar.widthAnchor.constraint(equalTo:stack.widthAnchor),scroll.widthAnchor.constraint(equalTo:stack.widthAnchor),scroll.heightAnchor.constraint(greaterThanOrEqualToConstant:150)])
            progressWindow=panel;progressTimer=Timer.scheduledTimer(withTimeInterval:1,repeats:true){[weak self] _ in self?.refreshProgress()}
        }
        if present {NSApp.activate(ignoringOtherApps:true);progressWindow?.makeKeyAndOrderFront(nil)};refreshProgress()
    }
    func refreshProgress() {
        let data=(try? Data(contentsOf:root.appendingPathComponent("install-progress.json"))) ?? Data()
        let value=((try? JSONSerialization.jsonObject(with:data)) as? [String:Any]) ?? [:]
        let phase=value["phase"] as? String ?? "checking"
        let names=["checking":"Checking managed runtimes","downloading":"Downloading","python":"Preparing Python","dependencies":"Preparing dependencies","n8n":"Preparing n8n","ready":"Managed runtimes verified"]
        var label=nativeText(names[phase] ?? "Preparing services")
        let total=value["total_bytes"] as? Int ?? 0,name=value["download_name"] as? String ?? ""
        let file=root.appendingPathComponent("downloads").appendingPathComponent((name as NSString).lastPathComponent)
        let attributes=try? FileManager.default.attributesOfItem(atPath:file.path),downloaded=(attributes?[.size] as? NSNumber)?.intValue ?? 0
        if phase == "downloading" {label += " · "+ByteCountFormatter.string(fromByteCount:Int64(downloaded),countStyle:.file)+" / "+(total>0 ? ByteCountFormatter.string(fromByteCount:Int64(total),countStyle:.file) : nativeText("Total size unavailable"))}
        progressLabel?.stringValue=label;progressBar?.isIndeterminate=total<=0 && busy
        if total>0 {progressBar?.doubleValue=min(100,Double(downloaded)*100/Double(total))} else if !busy {progressBar?.doubleValue=100}
        let statusData=(try? JSONSerialization.data(withJSONObject:latestStatus,options:[.prettyPrinted,.sortedKeys])) ?? Data();let statusText=String(data:statusData,encoding:.utf8) ?? ""
        progressText?.string=detailsVisible ? (try? String(contentsOf:detailLog,encoding:.utf8)).map { statusText+"\n\n"+String($0.suffix(16000)) } ?? statusText : nativeText("Background mode keeps your Mac awake on AC and battery while allowing the screen to lock and turn off. It uses battery between tasks. Start at Login restores the service after you sign in; explicit Stop stays stopped. Lid closure, system Sleep, shutdown and critical battery can interrupt schedules.")
    }
    @objc func installUpdate() {
        guard !busy else {return};let picker=NSOpenPanel();picker.title=nativeText("Choose a signed and notarized Sleep In.app");picker.canChooseFiles=true;picker.canChooseDirectories=false;picker.treatsFilePackagesAsDirectories=false;picker.allowedContentTypes=[.applicationBundle];picker.prompt=nativeText("Install update")
        guard picker.runModal() == .OK,let url=picker.url else {return}
        guard let stop=chooseStop() else {return};let mode=stop == "--stop-cancel" ? "cancel" : "finish"
        busy=true;showProgress();statusItem.title=nativeText("Update")
        DispatchQueue.global().async {let result=self.command("--update",capture:true,extra:[url.path,mode]);DispatchQueue.main.async {self.busy=false;self.refresh();self.showError(result.0 == 0 ? "Update complete" : "Update did not complete",result.0 == 0 ? "The updated background service is ready. Reopen the companion to load new menu features." : "Review update details. If readiness failed, the previous application and data were restored; the backup is retained.");self.progressText?.string=result.1}}
    }
    func presentAlert(_ alert:NSAlert)->NSApplication.ModalResponse {alert.runModal()}
    func showError(_ title:String,_ text:String) { let alert=NSAlert(); alert.messageText=nativeText(title); alert.informativeText=nativeText(text); _ = self.presentAlert(alert) }
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
    let evidence:[String:Any] = ["packaged_resources_ready":true,"termination_decisions":decisions,"side_effects_performed":false,"locales":["en","zh-CN"],"chinese_background_label":nativeChinese["Background running"]!,"update_requires_developer_id":true]
    let data=try! JSONSerialization.data(withJSONObject:evidence,options:[.sortedKeys])
    print(String(data:data,encoding:.utf8)!)
    exit(0)
}
let application=NSApplication.shared
let delegate=SleepInDelegate()
application.delegate=delegate
application.run()
