#!/usr/bin/ruby
require "digest"
require "fileutils"
require "find"
require "json"
require "open3"
require "tmpdir"
require "uri"

TAP = "toolbar-spike/retry".freeze
TOKEN = "pipecat-toolbar-lock-spike-retry".freeze
CASK = "#{TAP}/#{TOKEN}".freeze
APP = "Toolbar Lock Spike Retry.app".freeze
BREW_ENV = {
  "HOMEBREW_NO_AUTO_UPDATE" => "1",
  "HOMEBREW_NO_AUTOREMOVE" => "1",
  "HOMEBREW_NO_INSTALL_CLEANUP" => "1",
}.freeze

class Spike
  def initialize
    @brew = resolve_brew
  end

  def check
    prefix = brew_read("--prefix").strip
    formulae, casks = inventory
    puts "Brew version: #{brew_read("--version").lines.first.strip}"
    puts "Brew prefix: #{prefix}"
    puts "Installed formulae: #{formulae.size}; installed casks: #{casks.size}"
    unless formulae.empty? && casks.empty?
      raise "Use a separate disposable Brew prefix with zero formulae and casks. Checks ran read-only."
    end
    if brew_read("tap").lines.map(&:strip).include?(TAP)
      raise "The dedicated test tap already exists. Use a fresh disposable prefix or reset the guest snapshot."
    end
    puts "Inventory check passed. Run --run only in your disposable guest or test prefix."
  end

  def run
    check
    @state = Dir.mktmpdir("pipecat-cask-spike-r3-")
    @appdir = File.join(@state, "apps")
    FileUtils.mkdir(@appdir)
    @lock_path = File.join(@state, "operation.lock")
    @event_path = File.join(@state, "events.jsonl")
    @caskroom_path = File.join(brew_read("--caskroom").strip, TOKEN)
    raise "Fixture Caskroom path already exists" if File.exist?(@caskroom_path) || File.symlink?(@caskroom_path)
    puts "Evidence directory: #{@state}"
    fixtures = ["1.0", "2.0"].map { |version| make_cask(version) }
    @expected_source_sha = ["1.0", "2.0"].zip(fixtures.map { |path| Digest::SHA256.file(path).hexdigest }).to_h
    File.write(File.join(@state, "environment.txt"), "#{fixed("/usr/bin/sw_vers")}#{brew_read("--version")}")

    expect("create-tap", ["tap-new", "--no-git", TAP])
    expect("restore-normal-mode", ["developer", "off"])
    tap_path = brew_read("--repository", TAP).strip
    @tap_cask = File.join(tap_path, "Casks", "#{TOKEN}.rb")
    FileUtils.mkdir_p(File.dirname(@tap_cask))
    FileUtils.cp(fixtures[0], @tap_cask)
    trust_fixture("trust-fixture")

    expect("install-v1", ["install", "--cask", "--appdir=#{@appdir}", CASK],
           required_events: [["1.0", "install-preflight"], ["1.0", "install-postflight"]])
    verify_version("1.0")
    baseline = snapshot
    raise "Installed app or metadata could not be fingerprinted" unless baseline.values.all? { |tree| tree && !tree.empty? }
    save_snapshot("baseline", baseline)
    FileUtils.cp(fixtures[1], @tap_cask)
    refusal = File.join(@state, "refuse-quit")
    File.write(refusal, "refuse\n")

    expect("refuse-upgrade", ["upgrade", "--cask", CASK],
           failure: "spike: simulated quit refusal",
           required_events: [["1.0", "uninstall-preflight", "refuse"]])
    assert_same("refuse-upgrade", baseline)
    expect("refuse-uninstall", ["uninstall", "--cask", CASK],
           failure: "spike: simulated quit refusal",
           required_events: [["1.0", "uninstall-preflight", "refuse"]])
    # Homebrew 7.0.14 can remove individual trust even on a failed uninstall.
    # Restore trust for this fixture before the installed-version check loads it.
    trust_fixture("restore-fixture-trust")
    assert_same("refuse-uninstall", baseline)

    File.unlink(refusal)
    failure_marker = File.join(@state, "fail-v2")
    File.write(failure_marker, "fail after move\n")
    expect("rollback", ["upgrade", "--cask", CASK],
           failure: "spike: injected failure after successor app move",
           required_events: [["1.0", "uninstall-preflight"], ["2.0", "install-postflight"],
                             ["1.0", "install-postflight"]])
    assert_same("rollback", baseline)

    File.unlink(failure_marker)
    expect("upgrade", ["upgrade", "--cask", CASK],
           required_events: [["1.0", "uninstall-preflight"], ["2.0", "install-postflight"]])
    verify_version("2.0")
    expect("uninstall", ["uninstall", "--cask", CASK],
           required_events: [["2.0", "uninstall-preflight", "allow"]])
    raise "App remains after normal uninstall" if File.exist?(File.join(@appdir, APP))
    raise "Fixture remains installed after normal uninstall" unless inventory.last.empty?
    puts "All fixture assertions passed. Inspect #{@state}/events.jsonl and command logs."
  end

  private

  def resolve_brew
    raise "Run the spike on macOS" unless fixed("/usr/bin/uname", "-s").strip == "Darwin"
    ENV.fetch("PATH", "").split(File::PATH_SEPARATOR).each do |entry|
      next unless entry.start_with?("/")
      executable = File.join(entry, "brew")
      return File.realpath(executable) if File.file?(executable) && File.executable?(executable)
    end
    raise "Brew is missing from PATH"
  end

  def fixed(*argv)
    out, err, result = Open3.capture3(*argv)
    raise "#{argv.first} failed: #{err.strip}" unless result.success?
    out
  end

  def brew_read(*args)
    out, err, result = Open3.capture3(BREW_ENV, @brew, *args)
    raise "Brew #{args.join(" ")} failed: #{err.strip}" unless result.success?
    out
  end

  def inventory
    [brew_read("list", "--formula", "-1").split, brew_read("list", "--cask", "-1").split]
  end

  def check_fixture_inventory
    formulae, casks = inventory
    unless formulae.empty? && (casks - [TOKEN, CASK]).empty?
      raise "Unrelated Brew packages detected. Refusing to mutate this prefix."
    end
  end

  def make_cask(version)
    folder = File.join(@state, version)
    contents = File.join(folder, APP, "Contents")
    executable = File.join(contents, "MacOS", "spike")
    FileUtils.mkdir_p(File.dirname(executable))
    File.write(executable, "#!/bin/sh\nexit 0\n")
    File.chmod(0o755, executable)
    File.write(File.join(contents, "Info.plist"), <<~PLIST)
      <?xml version="1.0" encoding="UTF-8"?>
      <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
      <plist version="1.0"><dict>
      <key>CFBundleIdentifier</key><string>ai.pipecat.ToolbarLockSpikeRetry</string>
      <key>CFBundleExecutable</key><string>spike</string>
      <key>CFBundleName</key><string>Toolbar Lock Spike Retry</string>
      <key>CFBundlePackageType</key><string>APPL</string>
      <key>CFBundleVersion</key><string>#{version}</string>
      <key>CFBundleShortVersionString</key><string>#{version}</string>
      </dict></plist>
    PLIST
    zip = File.join(folder, "fixture.zip")
    fixed("/usr/bin/ditto", "-c", "-k", "--keepParent", File.join(folder, APP), zip)
    escaped_path = URI::DEFAULT_PARSER.escape(zip, /[^A-Za-z0-9\/_.~-]/)
    src = File.read(File.join(__dir__, "fixture.rb.template"))
    vars = { "STATE" => @state, "VERSION" => version, "SHA" => Digest::SHA256.file(zip).hexdigest,
             "URL" => "file://#{escaped_path}" }
    path = File.join(folder, "#{TOKEN}.rb")
    File.write(path, src.gsub(/@([A-Z]+)@/) { vars.fetch(Regexp.last_match(1)).dump })
    fixed("/usr/bin/ruby", "-c", path)
    path
  end

  def trust_fixture(label)
    expect(label, ["trust", "--cask", CASK])
  end

  def events
    return [] unless File.file?(@event_path)
    File.readlines(@event_path).map { |line| JSON.parse(line) }
  end

  def expect(label, args, failure: nil, required_events: [])
    check_fixture_inventory
    start = events.length
    out, status = Open3.capture2e(BREW_ENV, @brew, *args)
    log = File.join(@state, "#{label}.log")
    File.write(log, "$ brew #{args.join(" ")}\nexit=#{status.exitstatus}\n#{out}")
    observed = events.drop(start)
    File.write(File.join(@state, "#{label}.events.json"), JSON.pretty_generate(observed) + "\n")
    puts "#{label}: exit #{status.exitstatus}, #{observed.length} hook events"

    if !required_events.empty?
      _, lock_status = Open3.capture2e("/usr/bin/lockf", "-k", "-t", "0", @lock_path, "/usr/bin/true")
      raise "#{label}: lock unavailable after Brew exited" unless lock_status.success?
      raise "#{label}: lock not retained across hook boundaries" unless observed.empty? ||
        (observed.map { |e| e.values_at("pid", "lock_fd", "lock_inode") }.uniq.size == 1 &&
         observed.all? { |e| e["contender_exit"] == 75 })
      observed.each do |event|
        puts "  #{event["version"]} #{event["event"]}: marker=#{event["marker_present"]} decision=#{event["decision"]}"
      end
      unless observed.empty?
        @lock_inode ||= observed.first.fetch("lock_inode")
        raise "#{label}: the lock inode changed between invocations" unless observed.all? { |e| e["lock_inode"] == @lock_inode }
        unless observed.all? { |e| e["source_sha256"] == @expected_source_sha.fetch(e["version"]) }
          raise "#{label}: Brew loaded an unexpected fixture source. Inspect the event log."
        end
      end
    end

    if failure
      unless !status.success? && out.include?(failure)
        save_snapshot("#{label}-unexpected", snapshot)
        raise "#{label}: expected nonzero exit with '#{failure}'. Inspect #{log} and the event log."
      end
    elsif !status.success?
      raise "#{label}: unexpected failure. Inspect #{log}."
    end

    remaining = observed.dup
    required_events.each do |version, event_name, decision|
      index = remaining.index { |e| e["version"] == version && e["event"] == event_name &&
        (!decision || e["decision"] == decision) }
      raise "#{label}: missing event #{[version, event_name, decision].compact.join(" ")}" unless index
      remaining = remaining.drop(index + 1)
    end
  end

  def verify_version(expected)
    app_plist = File.join(@appdir, APP, "Contents", "Info.plist")
    actual = fixed("/usr/libexec/PlistBuddy", "-c", "Print :CFBundleVersion", app_plist).strip
    raise "Expected fixture version #{expected}, found #{actual}" unless actual == expected
    versions = brew_read("list", "--cask", "--versions", TOKEN).split
    raise "Brew did not report fixture version #{expected}" unless versions.include?(expected)
  end

  def tree_fingerprints(root)
    return nil unless File.directory?(root)
    Find.find(root).each_with_object({}) do |path, fingerprints|
      next if path == root
      key = path.delete_prefix(root + "/")
      if File.symlink?(path)
        fingerprints[key] = ["link", File.readlink(path)]
      elsif File.file?(path)
        fingerprints[key] = ["file", Digest::SHA256.file(path).hexdigest]
      else
        fingerprints[key] = ["directory"]
      end
    end.sort.to_h
  end

  def snapshot
    { "app" => tree_fingerprints(File.join(@appdir, APP)),
      "metadata" => tree_fingerprints(File.join(@caskroom_path, ".metadata")) }
  end

  def save_snapshot(label, data)
    File.write(File.join(@state, "#{label}.snapshot.json"), JSON.pretty_generate(data) + "\n")
  end

  def assert_same(label, baseline)
    actual = snapshot
    save_snapshot(label, actual)
    raise "#{label}: app bytes or installed cask metadata changed" unless actual == baseline
    verify_version("1.0")
    puts "#{label}: app and metadata match version 1 baseline"
  end
end

begin
  case ARGV
  when [], ["--check"] then Spike.new.check
  when ["--run"] then Spike.new.run
  else abort "Usage: ruby run-spike.rb [--check | --run]"
  end
rescue StandardError => error
  warn "SPIKE STOPPED: #{error.message}"
  exit 1
end
