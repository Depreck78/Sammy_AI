import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ChangeEvent, KeyboardEvent, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import remarkGfm from "remark-gfm";
import {
  AlertTriangle,
  Archive,
  AudioLines,
  Bot,
  BriefcaseBusiness,
  Brain,
  ChevronDown,
  Circle,
  Check,
  Code,
  ContactRound,
  Copy,
  Cpu,
  Database,
  Download,
  FileSpreadsheet,
  FileUp,
  FolderOpen,
  Github,
  Globe,
  GraduationCap,
  Heart,
  Lightbulb,
  ListFilter,
  Mail,
  Mic,
  MicOff,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  PenLine,
  Pin,
  PinOff,
  Plug,
  Plus,
  RefreshCcw,
  Rocket,
  Search,
  Send,
  Settings,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Smartphone,
  Sparkles,
  Square,
  Table2,
  Trash2,
  Volume2,
  Wrench,
  X,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { LANGUAGES, useT, voicePrefixFor } from "./i18n";
// The Eagle voice-auth module (incl. its WASM) is imported lazily where used, so it only
// downloads when someone actually turns on voice authentication.
import type { VoiceAuthRecognizer } from "./voiceAuth";

const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const SAMMY_LOGO = "/sammy-logo.png";
const VOICE_URI_KEY = "sammy-voice-uri";
const VOICE_RATE_KEY = "sammy-voice-rate";
const WAKE_GREETING_STORAGE_KEY = "sammy-wake-greeting-at";
const WAKE_GREETING_RESET_MS = 3 * 60 * 60 * 1000;
// A touch above 1.0 so Sammy reads replies in a brighter, friendlier register.
const CUTE_PITCH = 1.15;
// Spoken immediately when a voice command is heard, so Sammy feels responsive while it thinks.
const VOICE_ACKS = [
  "Got it — working on it!",
  "On it!",
  "Sure, give me a sec.",
  "Okay, let me look into that.",
  "Got it, one moment.",
  "Alright, on it now.",
];
const SPOKEN_SUMMARY_MAX_WORDS = 44;
const SPOKEN_SHORT_REPLY_MAX_WORDS = 52;
const SPOKEN_SHORT_REPLY_MAX_CHARS = 360;
const SPOKEN_SUMMARY_DETAIL_HINT = "I put the details in the chat.";
const FULL_SPEECH_MAX_CHARS = 5000;
// After the first "Sammy …", the user stays "engaged" and can keep talking without the wake
// word. Engagement re-arms (wake word required again) after this much silence.
const ENGAGE_WINDOW_MS = 45000;
// Voice authentication (Picovoice Eagle): accept a command only if the enrolled owner's voice
// scored at least this much within OWNER_RECENT_MS before the transcript finalized.
const VOICE_AUTH_MATCH_THRESHOLD = 0.5;
const OWNER_RECENT_MS = 1500;
const voiceAuthSupported = () =>
  typeof window !== "undefined" &&
  Boolean(navigator.mediaDevices?.getUserMedia) &&
  typeof WebAssembly !== "undefined";
// Sammy should only ever offer warm, cute female voices — never the robotic/novelty system
// voices (Zarvox, Trinoids, Albert, Bad News…) or the male ones. The Web Speech API doesn't
// expose a reliable gender flag on SpeechSynthesisVoice across Safari/Chrome, so we match on
// the voice's given name. Names are matched after stripping any " (Enhanced)"/locale suffix.
const CUTE_FEMALE_VOICE_NAMES = new Set([
  // English
  "samantha", "victoria", "allison", "ava", "susan", "karen", "moira", "tessa", "fiona",
  "kate", "serena", "stephanie", "catherine", "zoe", "isha", "joelle", "nicky", "noelle", "veena",
  // Spanish / Catalan
  "monica", "paulina", "marisol", "angelica", "soledad", "esperanza", "carmela", "isabela", "montse", "sara",
  // French
  "amelie", "audrey", "aurelie", "marie", "chantal",
  // German
  "anna", "petra", "helena", "martina",
  // Italian
  "alice", "federica", "paola", "chiara", "emma", "silvia",
  // Portuguese
  "luciana", "joana", "catarina", "fernanda",
  // Japanese
  "kyoko", "o-ren",
  // Chinese
  "ting-ting", "mei-jia", "sin-ji", "tian-tian", "li-mu", "yu-shu", "mei-ling",
  // Korean
  "yuna", "sora", "suhyun",
  // Russian / Eastern Europe
  "milena", "katya", "zuzana", "iveta", "zosia", "ewa", "maja", "ioana", "mariska", "laura",
  // Nordic
  "alva", "klara", "nora", "ida", "satu", "ellen", "claire",
  // Other
  "yelda", "melina", "carmit", "laila", "lekha", "kanya", "narisa", "damayanti", "linh",
]);

// Base name without the "(Enhanced)"/"(Premium)"/locale parenthetical, lowercased.
const baseVoiceName = (name: string) => name.replace(/\(.*?\)/g, "").trim().toLowerCase();

// Keep only cute female voices. We also honor explicit "...Female" labels (e.g. Chrome's
// "Google UK English Female") and drop explicit "...Male" ones.
const isCuteFemaleVoice = (voice: SpeechSynthesisVoice) => {
  const lower = voice.name.toLowerCase();
  if (lower.includes("female")) return true;
  if (lower.includes("male")) return false; // checked after "female" since it's a substring
  return CUTE_FEMALE_VOICE_NAMES.has(baseVoiceName(voice.name));
};

// High-quality downloadable macOS voices (e.g. "Ava (Premium)").
const isPremiumVoice = (voice: SpeechSynthesisVoice) => /\bpremium\b/i.test(voice.name);
// macOS Siri voices, when the OS exposes them to apps.
const isSiriVoice = (voice: SpeechSynthesisVoice) => /\bsiri\b/i.test(voice.name);

// Does this voice speak the given app language? (BCP-47 prefix, e.g. "es" matches "es-MX".)
const voiceMatchesLang = (voice: SpeechSynthesisVoice, prefix: string) =>
  voice.lang.toLowerCase().replace("_", "-").startsWith(prefix.toLowerCase());

// All installed voices for the chosen app language (no quality/gender filtering).
const voicesForLang = (list: SpeechSynthesisVoice[], prefix: string) =>
  list.filter((voice) => voiceMatchesLang(voice, prefix));

// Pick a sensible default when the user hasn't chosen one: best quality first (premium → siri →
// anything), preferring the app language.
const pickDefaultCuteVoice = (list: SpeechSynthesisVoice[], prefix?: string) => {
  const inLang = prefix ? list.filter((voice) => voiceMatchesLang(voice, prefix)) : list;
  const pool = inLang.length ? inLang : list;
  return pool.find(isPremiumVoice) ?? pool.find(isSiriVoice) ?? pool[0];
};
// Optional greeting before the wake word, per app language. English is always allowed too, since
// speech recognition (and people) often mix languages.
const WAKE_GREETINGS: Record<string, string[]> = {
  en: ["hey", "hi", "ok", "okay", "yo"],
  es: ["hola", "oye", "ey", "buenas"],
  zh: ["你好", "您好", "嗨", "嘿"],
};
const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
// Wake-word matcher for the active identity, tolerant of a leading greeting in the selected
// language ("hi Sammy" / "hola Sammy" / "你好 Sammy") or no greeting at all. Sammy keeps its
// fuzzy mishearing variants (Sammie / Sammi / Sammey).
function wakeWordRe(name: string, lang = "en"): RegExp {
  const trimmed = name.trim();
  const namePart =
    !trimmed || trimmed.toLowerCase() === "sammy"
      ? "samm(?:y|ie|i|ey|e)(?:'s)?"
      : `${escapeRegExp(trimmed)}(?:'s)?`;
  const greets = Array.from(new Set([...(WAKE_GREETINGS[lang] ?? []), ...WAKE_GREETINGS.en]));
  const greetAlt = greets.map(escapeRegExp).join("|");
  return new RegExp(`(?:\\b(?:${greetAlt})\\b[\\s,]*)?\\b${namePart}\\b[\\s,.:;!?-]*(.*)`, "i");
}

type ModelInfo = {
  name: string;
  size: string;
  parameter_size?: string;
  quantization_level?: string;
};

type Conversation = {
  id: string;
  title: string;
  model?: string;
  agent_id?: string;
  mode?: "chat" | "tool_builder";
  pinned: number;
  created_at: string;
  updated_at: string;
  preview?: string;
};

type Message = {
  id: string;
  conversation_id?: string;
  role: "user" | "assistant" | "tool";
  content: string;
  metadata: Record<string, any>;
  created_at: string;
};

type Agent = {
  id: string;
  name: string;
  system_prompt: string;
  model: string;
  icon?: string;
  enabled_tools: string[];
};

type Bestie = {
  id: string;
  name: string;
  personality: string;
  avatar: string; // upload file id of the (stylized) avatar, or "" for none
  created_at?: string;
  updated_at?: string;
};

type NetworkInfo = {
  port: number;
  lan_ip: string;
  lan_url: string;
  local_hostname: string;
  local_url: string;
  alias: string;
  alias_url: string;
  lan_active: boolean;
  tailscale_active: boolean;
  tailscale_url: string;
  tailscale_https_url: string;
};

type ToolInfo = {
  name: string;
  display_name: string;
  description: string;
  icon: string;
  requires_auth: boolean;
  connected: boolean;
  saved_auth_fields?: Record<string, boolean>;
  auth_credentials?: Record<string, string>;
  auth_fields: Array<{
    name: string;
    label: string;
    type: string;
    placeholder?: string;
    description?: string;
    options?: string[];
  }>;
  functions: unknown[];
  kind?: "sammy_plugin" | "external_plugin" | "codex_plugin";
  status_message?: string;
  plugin?: {
    name: string;
    version: string;
    root?: string;
    manifest?: string;
    source?: "sammy_builtin" | "sammy_home" | "local" | "codex_cache" | "explicit" | string;
    brand_color?: string;
    developer?: string;
    category?: string;
    capabilities?: string[];
    default_prompt?: string[];
  };
  skills?: Array<{ name: string; description: string; path: string }>;
  mcp_servers?: Array<{ name: string }>;
  app_connectors?: Array<{ name: string; connector_id: string }>;
  callable?: boolean;
  compatibility?: {
    status: string;
    label: string;
    detail: string;
    callable: boolean;
    adapter_name?: string;
    adapter_display_name?: string;
    adapter_connected?: boolean;
    adapter_functions?: number;
  };
};

type SettingsShape = {
  default_model: string;
  system_prompt: string;
  num_ctx: number;
  num_predict: number;
  temperature: number;
  think: boolean;
  theme: "dark" | "light";
  access_password_enabled: boolean;
  memory_mode: "auto" | "ask" | "off";
  memory_recall_enabled: boolean;
  memory_recall_limit: number;
  elevenlabs_enabled: boolean;
  elevenlabs_configured: boolean;
  elevenlabs_voice_id: string;
  voice_auth_enabled: boolean;
  picovoice_access_key: string;
  picovoice_speaker_profile: string;
  active_bestie_id: string;
  gemini_configured: boolean;
};

type ElevenLabsVoice = {
  voice_id: string;
  name: string;
  category?: string;
  labels?: Record<string, string>;
  preview_url?: string;
};

type MemoryEntry = {
  id: string;
  scope: "soul" | "user" | "agent";
  agent_id: string;
  kind: string;
  content: string;
  status: "active" | "pending" | "archived";
  confidence: number;
  sensitive: boolean;
  source_label: string;
  source_conversation_title?: string;
  expires_at?: string;
  use_count: number;
  created_at: string;
};

type MemoryResponse = {
  memories: MemoryEntry[];
  stats: { active: number; pending: number; archived: number };
};

type AuthStatus = {
  password_required: boolean;
  authenticated: boolean;
};

type AnySettingValue = string | number | boolean;

type ToolEvent = {
  type: "start" | "result" | "memory";
  name: string;
  tool?: string;
  tool_display_name?: string;
  memory_file?: string;
  arguments?: Record<string, any>;
  content?: string;
  requires_reconnect?: boolean;
  count?: number;
  received_at?: number;
};

type GenerationPhase =
  | "starting"
  | "writing"
  | "continuing"
  | "compacting"
  | "tool"
  | "reconnecting"
  | "stopping"
  | "complete";

type GenerationState = {
  phase: GenerationPhase;
  label: string;
  detail?: string;
  part: number;
  tool_step: number;
};

type ChatJobSnapshot = {
  id: string;
  conversation_id: string;
  agent_id: string;
  model: string;
  user_message_id: string;
  status: "queued" | "running" | "complete" | "error" | "stopped";
  phase: GenerationPhase | "error" | "stopped";
  part: number;
  tool_step: number;
  last_event_id: number;
  final_message?: Message;
  error?: string;
};

type ChatJobCreateResponse = {
  job: ChatJobSnapshot;
  conversation: Conversation;
  user_message?: Message;
};

const iconMap: Record<string, LucideIcon> = {
  Mail,
  FolderOpen,
  FileSpreadsheet,
  Search,
  Table2,
  BriefcaseBusiness,
  ContactRound,
  Plug,
  Github,
  Wrench,
};

const AGENT_ICON_LIBRARY: Array<{ id: string; icon: LucideIcon; label: string }> = [
  { id: "brain", icon: Brain, label: "Brain" },
  { id: "bot", icon: Bot, label: "Bot" },
  { id: "sparkles", icon: Sparkles, label: "Sparkles" },
  { id: "mail", icon: Mail, label: "Mail" },
  { id: "briefcase", icon: BriefcaseBusiness, label: "Briefcase" },
  { id: "contact", icon: ContactRound, label: "Contact" },
  { id: "github", icon: Github, label: "GitHub" },
  { id: "search", icon: Search, label: "Search" },
  { id: "folder", icon: FolderOpen, label: "Folder" },
  { id: "plug", icon: Plug, label: "Plug" },
  { id: "wrench", icon: Wrench, label: "Wrench" },
  { id: "settings", icon: Settings, label: "Settings" },
  { id: "mic", icon: Mic, label: "Mic" },
  { id: "code", icon: Code, label: "Code" },
  { id: "pen", icon: PenLine, label: "Pen" },
  { id: "graduation", icon: GraduationCap, label: "Graduation" },
  { id: "lightbulb", icon: Lightbulb, label: "Lightbulb" },
  { id: "rocket", icon: Rocket, label: "Rocket" },
  { id: "shield", icon: Shield, label: "Shield" },
  { id: "heart", icon: Heart, label: "Heart" },
  { id: "palette", icon: Palette, label: "Palette" },
  { id: "cpu", icon: Cpu, label: "CPU" },
  { id: "globe", icon: Globe, label: "Globe" },
];

const agentIconLibraryMap = Object.fromEntries(AGENT_ICON_LIBRARY.map((item) => [item.id, item.icon])) as Record<
  string,
  LucideIcon
>;

const MIN_TOOL_USING_MS = 650;
const ACTIVE_CHAT_JOB_KEY = "sammy.active-chat-job";

// The active identity (built-in Sammy, or the user's chosen bestie). App provides the live
// value; sibling components (message bubbles, empty state, etc.) read it via useIdentity().
const IdentityContext = createContext<{ name: string; avatarUrl: string }>({
  name: "Sammy",
  avatarUrl: SAMMY_LOGO,
});
const useIdentity = () => useContext(IdentityContext);

function SammyLogo({
  className = "h-5 w-5",
  withShadow = true,
  src = SAMMY_LOGO,
  alt = "Sammy",
}: {
  className?: string;
  withShadow?: boolean;
  src?: string;
  alt?: string;
}) {
  return (
    <img
      src={src}
      alt={alt}
      className={[
        className,
        "rounded-full bg-white object-contain",
        withShadow ? "shadow-[0_8px_22px_rgba(92,61,44,0.12)]" : "",
      ].join(" ")}
      draggable={false}
    />
  );
}

async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: options.credentials ?? "include",
    headers: {
      "Content-Type": "application/json",
      ...(options.headers ?? {}),
    },
  });
  if (
    response.status === 401 &&
    response.headers.get("X-Sammy-Auth-Required") === "true" &&
    typeof window !== "undefined"
  ) {
    window.dispatchEvent(new CustomEvent("sammy-auth-required"));
  }
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || response.statusText);
  }
  return response.json();
}

function formatModel(model: ModelInfo) {
  const details = [model.parameter_size, model.quantization_level, model.size].filter(Boolean).join(" · ");
  return details ? `${model.name} (${details})` : model.name;
}

function resolveModelName(preferredModel: string | undefined, availableModels: ModelInfo[]) {
  const names = availableModels.map((model) => model.name);
  const nameSet = new Set(names);
  const preferred = preferredModel || "";
  if (preferred && nameSet.has(preferred)) return preferred;

  const candidates = new Set<string>();
  if (preferred) {
    candidates.add(preferred.replace(/^jarvis-/, "sammy-"));
    candidates.add(preferred.replace(/jarvis/gi, "sammy"));
    if (!preferred.includes(":")) candidates.add(`${preferred}:latest`);
  }

  for (const candidate of candidates) {
    if (nameSet.has(candidate)) return candidate;
  }
  return names[0] || preferred;
}

function toolSourceLabel(tool: ToolInfo) {
  if (tool.plugin?.source === "sammy_home") return "Sammy Tool";
  if (tool.plugin?.source === "local") return "Local Tool";
  if (tool.plugin?.source === "codex_cache") return "Compatibility Cache";
  if (tool.plugin?.source === "explicit") return "Explicit Path";
  return tool.kind === "external_plugin" || tool.kind === "codex_plugin" ? "External Tool" : "Sammy Tool";
}

function toolCapabilityLine(tool: ToolInfo) {
  const bridgedFunctions =
    tool.compatibility?.status === "bridged" && tool.compatibility.adapter_functions
      ? `${tool.compatibility.adapter_functions} bridged functions`
      : "";
  const counts = [
    tool.functions?.length ? `${tool.functions.length} functions` : bridgedFunctions,
    tool.skills?.length ? `${tool.skills.length} skills` : "",
    tool.mcp_servers?.length ? `${tool.mcp_servers.length} MCP` : "",
    tool.app_connectors?.length ? `${tool.app_connectors.length} app` : "",
  ].filter(Boolean);
  return counts.join(" · ") || "Context only";
}

function toolRuntimeLabel(tool: ToolInfo) {
  return tool.compatibility?.label || (tool.functions?.length ? "Callable" : "Context only");
}

function installLabel(tool: ToolInfo) {
  if (tool.plugin?.source === "sammy_home") return "Installed in Sammy";
  if (tool.plugin?.source === "local") return "Installed locally";
  if (tool.plugin?.source === "codex_cache") return "Optional compatibility";
  if (tool.plugin?.source === "explicit") return "Explicit tool path";
  return "Built into Sammy";
}

function hasSavedAuth(tool: ToolInfo) {
  return Boolean(tool.saved_auth_fields && Object.values(tool.saved_auth_fields).some(Boolean));
}

function authStatusLabel(tool: ToolInfo) {
  if (!tool.requires_auth) return "No auth";
  if (tool.connected) return "Connected";
  if (hasSavedAuth(tool)) return "Needs reconnect";
  return "Needs auth";
}

function needsPluginReconnect(tool: ToolInfo) {
  return tool.requires_auth && !tool.connected;
}

function hasSavedOAuthClient(tool: ToolInfo) {
  return Boolean(tool.saved_auth_fields?.client_id && tool.saved_auth_fields?.client_secret);
}

function credentialsFromTools(tools: ToolInfo[]) {
  const drafts: Record<string, Record<string, string>> = {};
  tools.forEach((tool) => {
    drafts[tool.name] = { ...(tool.auth_credentials ?? {}) };
  });
  return drafts;
}

function toolDirectoryCategory(tool: ToolInfo) {
  const explicit = String(tool.plugin?.category || "").trim();
  if (explicit && explicit.toLowerCase() !== "built-in") return explicit;
  const name = `${tool.name} ${tool.display_name}`.toLowerCase();
  if (name.includes("mail") || name.includes("gmail")) return "Communication";
  if (name.includes("crm") || name.includes("contact") || name.includes("sales")) return "Business & Operations";
  if (name.includes("github") || name.includes("code") || name.includes("web") || name.includes("search")) {
    return "Research & Development";
  }
  if (name.includes("file") || name.includes("folder") || name.includes("document") || name.includes("spreadsheet") || name.includes("excel") || name.includes("numbers")) return "Files & Workspace";
  return "Utilities";
}

const toolLogoPaths: Record<string, string> = {
  filesystem: "/tool-logos/filesystem.svg",
  github: "/tool-logos/github.svg",
  gmail: "/tool-logos/gmail.svg",
  google_contacts: "/tool-logos/google-contacts.svg",
  web_search: "/tool-logos/web-search.svg",
  excel: "/tool-logos/excel.svg",
  numbers: "/tool-logos/numbers.svg",
  zoho_crm: "/tool-logos/zoho-crm.svg",
  zoho_mail: "/tool-logos/zoho-mail.svg",
};

function ToolGlyph({ tool, size = "large" }: { tool: ToolInfo; size?: "compact" | "small" | "large" }) {
  const Icon = iconMap[tool.icon] ?? Wrench;
  const logoPath = toolLogoPaths[tool.name];
  const brandColor = tool.plugin?.brand_color || (tool.connected ? "var(--accent)" : "var(--muted)");
  const dimensions =
    size === "large" ? "h-12 w-12 p-2" : size === "small" ? "h-10 w-10 p-1.5" : "h-8 w-8 p-1";
  const iconSize = size === "large" ? 22 : size === "small" ? 18 : 16;
  return (
    <span
      className={[
        "flex shrink-0 items-center justify-center overflow-hidden rounded-lg border border-[var(--line)] bg-white",
        dimensions,
      ].join(" ")}
      style={{ color: brandColor }}
    >
      {logoPath ? (
        <img src={logoPath} alt="" className="h-full w-full object-contain" draggable={false} />
      ) : (
        <Icon size={iconSize} />
      )}
    </span>
  );
}

function ToolsPage({
  tools,
  activeToolNames,
  agentName,
  onToggle,
  onManage,
  onCreateTool,
}: {
  tools: ToolInfo[];
  activeToolNames: string[];
  agentName: string;
  onToggle: (toolName: string, enabled: boolean) => void;
  onManage: () => void;
  onCreateTool: () => void;
}) {
  const { t } = useT();
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"all" | "built-in" | "local">("all");
  const [statusFilter, setStatusFilter] = useState<"all" | "added" | "setup">("all");
  const [filterOpen, setFilterOpen] = useState(false);
  const activeSet = useMemo(() => new Set(activeToolNames), [activeToolNames]);
  const addedTools = tools.filter((tool) => activeSet.has(tool.name));
  const normalizedQuery = query.trim().toLowerCase();
  const filteredTools = tools.filter((tool) => {
    const isBuiltIn = tool.plugin?.source === "sammy_builtin" || tool.kind === "sammy_plugin";
    const isLocal = ["sammy_home", "local", "explicit"].includes(String(tool.plugin?.source || ""));
    if (source === "built-in" && !isBuiltIn) return false;
    if (source === "local" && !isLocal) return false;
    if (statusFilter === "added" && !activeSet.has(tool.name)) return false;
    if (statusFilter === "setup" && !needsPluginReconnect(tool)) return false;
    if (!normalizedQuery) return true;
    return `${tool.display_name} ${tool.description} ${toolDirectoryCategory(tool)} ${toolCapabilityLine(tool)}`
      .toLowerCase()
      .includes(normalizedQuery);
  });
  const categoryOrder = ["Featured", "Communication", "Business & Operations", "Research & Development", "Files & Workspace", "Utilities"];
  const grouped = Array.from(
    filteredTools.reduce((groups, tool) => {
      const category = toolDirectoryCategory(tool);
      groups.set(category, [...(groups.get(category) ?? []), tool]);
      return groups;
    }, new Map<string, ToolInfo[]>())
  ).sort(([left], [right]) => {
    const leftIndex = categoryOrder.indexOf(left);
    const rightIndex = categoryOrder.indexOf(right);
    return (leftIndex < 0 ? 99 : leftIndex) - (rightIndex < 0 ? 99 : rightIndex) || left.localeCompare(right);
  });

  return (
    <section data-testid="tools-page" className="scrollbar min-h-0 flex-1 overflow-y-auto px-4 py-7 sm:px-7 sm:py-9 lg:px-10">
      <div className="mx-auto w-full max-w-6xl">
        <div>
          <h1 className="font-display text-3xl font-semibold text-[var(--ink)] sm:text-4xl">{t("Tools")}</h1>
          <p className="mt-1.5 text-base text-[var(--muted)]">{t("Give Sammy the tools needed for the work at hand.")}</p>
        </div>

        <div className="mt-7 flex items-center gap-2.5">
          <label className="relative min-w-0 flex-1">
            <Search size={18} className="pointer-events-none absolute left-4 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={t("Search tools and capabilities")}
              className="h-12 w-full rounded-xl border border-[var(--control-glass-border)] bg-[var(--control-glass)] pl-11 pr-4 text-sm outline-none backdrop-blur-md transition-colors placeholder:text-[var(--muted)] hover:border-[var(--line-strong)] focus:border-[var(--accent)] focus:bg-[var(--surface)]"
            />
          </label>
          <div className="relative">
            <button
              type="button"
              className={[
                "flex h-12 w-12 items-center justify-center rounded-xl border text-[var(--muted)] hover:text-[var(--ink)]",
                filterOpen || statusFilter !== "all"
                  ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)]"
                  : "border-transparent bg-[var(--inset)]",
              ].join(" ")}
              onClick={() => setFilterOpen((open) => !open)}
              title={t("Filter tools")}
              aria-label={t("Filter tools")}
            >
              <ListFilter size={18} />
            </button>
            {filterOpen ? (
              <div className="absolute right-0 top-14 z-20 w-44 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-1.5 shadow-popover">
                {(["all", "added", "setup"] as const).map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    className={[
                      "flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm",
                      statusFilter === filter ? "bg-[var(--accent-soft)] text-[var(--ink)]" : "text-[var(--muted)] hover:bg-[var(--surface-2)]",
                    ].join(" ")}
                    onClick={() => {
                      setStatusFilter(filter);
                      setFilterOpen(false);
                    }}
                  >
                    <span>{t(filter === "all" ? "All tools" : filter === "added" ? "Added tools" : "Needs setup")}</span>
                    {statusFilter === filter ? <Check size={14} className="text-[var(--accent)]" /> : null}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>

        <section className="mt-7 flex flex-col gap-4 border-y border-[var(--separator)] py-5 sm:flex-row sm:items-center">
          <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--accent)]">
            <Wrench size={20} />
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="text-base font-semibold text-[var(--ink)]">{t("Create your own tool")}</h2>
            <p className="mt-1 text-sm leading-5 text-[var(--muted)]">
              {t("Describe the service and what Sammy should do. Sammy will verify, build, and connect the tool locally.")}
            </p>
          </div>
          <button
            type="button"
            onClick={onCreateTool}
            className="flex h-10 shrink-0 items-center justify-center gap-2 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-4 text-sm font-semibold text-[var(--ink)] shadow-lift hover:bg-[var(--surface-2)]"
          >
            <Plus size={15} />
            {t("Start building")}
          </button>
        </section>

        <div className="mt-9 border-b border-[var(--separator)] pb-5">
          <div className="flex items-center justify-between gap-3">
            <div>
              <h2 className="text-lg font-semibold text-[var(--ink)]">{t("Added")}</h2>
              <p className="mt-0.5 text-xs text-[var(--muted)]">{t("Available to {agent}", { agent: agentName })}</p>
            </div>
            <button type="button" className="text-sm font-medium text-[var(--muted)] hover:text-[var(--ink)]" onClick={onManage}>
              {t("Manage")}
            </button>
          </div>
          <div className="mt-4 flex min-h-10 flex-wrap gap-2">
            {addedTools.length ? (
              addedTools.map((tool) => (
                <button
                  key={tool.name}
                  type="button"
                  title={tool.display_name}
                  aria-label={tool.display_name}
                  onClick={() => onToggle(tool.name, false)}
                  className="rounded-lg hover:bg-[var(--surface-2)]"
                >
                  <ToolGlyph tool={tool} size="small" />
                </button>
              ))
            ) : (
              <span className="self-center text-sm text-[var(--muted)]">{t("No tools added to this agent yet.")}</span>
            )}
          </div>
        </div>

        <div className="mt-5 flex gap-1 overflow-x-auto pb-1">
          {(["all", "built-in", "local"] as const).map((value) => (
            <button
              key={value}
              type="button"
              onClick={() => setSource(value)}
              className={[
                "shrink-0 rounded-lg px-3 py-2 text-sm font-medium",
                source === value ? "bg-[var(--surface-2)] text-[var(--ink)]" : "text-[var(--muted)] hover:text-[var(--ink)]",
              ].join(" ")}
            >
              {t(value === "all" ? "All" : value === "built-in" ? "Built into Sammy" : "Local")}
            </button>
          ))}
        </div>

        <div className="mt-7 space-y-9">
          {grouped.map(([category, categoryTools]) => (
            <section key={category}>
              <h2 className="border-b border-[var(--separator)] pb-3 text-lg font-semibold text-[var(--ink)]">{t(category)}</h2>
              <div className="mt-2 grid gap-x-10 md:grid-cols-2">
                {categoryTools.map((tool) => {
                  const active = activeSet.has(tool.name);
                  const needsSetup = needsPluginReconnect(tool);
                  return (
                    <div
                      key={tool.name}
                      data-testid={`tool-row-${tool.name}`}
                      className="group grid min-h-[88px] grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 rounded-xl px-3 py-3 hover:bg-[var(--surface-2)]"
                    >
                      <ToolGlyph tool={tool} />
                      <div className="min-w-0">
                        <div className="flex min-w-0 items-center gap-2">
                          <h3 className="truncate text-base font-semibold text-[var(--ink)]">{tool.display_name}</h3>
                          {active ? <Circle size={8} className="shrink-0 fill-[var(--green)] text-[var(--green)]" /> : null}
                        </div>
                        <p className="mt-0.5 line-clamp-2 text-sm leading-5 text-[var(--muted)]">{tool.description}</p>
                        <p className="mt-1 truncate font-mono text-[0.66rem] text-[var(--muted)]">
                          {t(toolSourceLabel(tool))} · {t(toolCapabilityLine(tool))}
                        </p>
                      </div>
                      {needsSetup && active ? (
                        <button
                          type="button"
                          onClick={onManage}
                          className="shrink-0 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm font-medium text-[var(--red)] hover:bg-[var(--inset)]"
                        >
                          {t("Setup")}
                        </button>
                      ) : (
                        <button
                          type="button"
                          onClick={() => onToggle(tool.name, !active)}
                          className={[
                            "shrink-0 rounded-lg border px-3 py-2 text-sm font-medium",
                            active
                              ? "border-transparent text-[var(--muted)] hover:border-[var(--line)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                              : "border-[var(--line)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--inset)]",
                          ].join(" ")}
                        >
                          {active ? t("Added") : t("Add")}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
          {!grouped.length ? (
            <div className="border-y border-[var(--separator)] py-12 text-center">
              <Wrench size={24} className="mx-auto text-[var(--muted)]" />
              <p className="mt-3 text-sm text-[var(--muted)]">{t("No tools match this search.")}</p>
            </div>
          ) : null}
        </div>
      </div>
    </section>
  );
}

function shortPath(path?: string) {
  if (!path) return "";
  return path.replace(/^\/Users\/[^/]+/, "~");
}

function enabledAgentNames(tool: ToolInfo, agents: Agent[]) {
  return agents.filter((agent) => agent.enabled_tools.includes(tool.name)).map((agent) => agent.name);
}

function groupConversationsByAgent(conversations: Conversation[], agents: Agent[]) {
  const agentById = new Map(agents.map((agent) => [agent.id, agent]));
  const agentOrder = new Map(agents.map((agent, index) => [agent.id, index]));
  const groups = new Map<string, { key: string; label: string; agent?: Agent; items: Conversation[] }>();

  conversations.forEach((conversation) => {
    const agentId = conversation.agent_id || "default";
    const agent = agentById.get(agentId);
    const key = agent?.id || agentId || "other";
    const existing =
      groups.get(key) ??
      {
        key,
        label: agent?.name || "Other agents",
        agent,
        items: [],
      };
    existing.items.push(conversation);
    groups.set(key, existing);
  });

  return Array.from(groups.values()).sort((left, right) => {
    const leftOrder = agentOrder.get(left.key) ?? Number.MAX_SAFE_INTEGER;
    const rightOrder = agentOrder.get(right.key) ?? Number.MAX_SAFE_INTEGER;
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return left.label.localeCompare(right.label);
  });
}

function parseSseBlock(block: string) {
  let event = "message";
  let id = 0;
  const dataLines: string[] = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("id:")) id = Number(line.slice(3).trim()) || 0;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  });
  if (!dataLines.length) return null;
  return { id, event, data: JSON.parse(dataLines.join("\n")) };
}

function updateStreamingMessage(
  items: Message[],
  id: string,
  updater: (message: Message) => Message
) {
  return items.map((message) => (message.id === id ? updater(message) : message));
}

function responseNoticeForMessage(message: Message) {
  const metadata = message.metadata ?? {};
  const status = metadata.response_status as string | undefined;
  const finishReason = metadata.finish_reason as string | undefined;
  const detail = metadata.response_notice as string | undefined;
  const error = metadata.response_error as string | undefined;

  if (!status || status === "complete" || status === "streaming") return null;

  if (status === "stopped") {
    return {
      tone: "neutral",
      title: "Reply stopped",
      detail: detail || "Generation was stopped before Sammy finished.",
    };
  }

  if (status === "empty") {
    return {
      tone: "warning",
      title: "No final reply was produced",
      detail: detail || "The model produced reasoning but did not return a final answer.",
    };
  }

  if (status === "error") {
    return {
      tone: "error",
      title: message.content.trim() ? "Reply ended with an error" : "Sammy could not produce a reply",
      detail: error || detail || "The stream ended before Sammy could finish.",
    };
  }

  if (status === "partial" || finishReason === "length") {
    return {
      tone: "warning",
      title: "Reply may be incomplete",
      detail:
        detail ||
        "Sammy did not receive a clean completion signal from the model, so the last response may be cut off.",
    };
  }

  return null;
}

function ResponseReliabilityNotice({ message }: { message: Message }) {
  const { t } = useT();
  const notice = responseNoticeForMessage(message);
  if (!notice) return null;
  const isError = notice.tone === "error";
  const isNeutral = notice.tone === "neutral";
  return (
    <div
      className={[
        "mt-3 flex max-w-full items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        isError
          ? "border-[var(--red)] bg-[var(--surface)]"
          : isNeutral
            ? "border-[var(--line)] bg-[var(--surface)]"
            : "border-[var(--accent)] bg-[var(--accent-soft)]",
      ].join(" ")}
    >
      <span
        className={[
          "mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-md",
          isError ? "text-[var(--red)]" : isNeutral ? "text-[var(--muted)]" : "text-[var(--accent)]",
        ].join(" ")}
      >
        <AlertTriangle size={14} />
      </span>
      <span className="min-w-0">
        <span className={["block font-medium", isError ? "text-[var(--red)]" : "text-[var(--ink)]"].join(" ")}>
          {t(notice.title)}
        </span>
        <span className="mt-0.5 block [overflow-wrap:anywhere] text-xs leading-relaxed text-[var(--muted)]">
          {t(notice.detail)}
        </span>
      </span>
    </div>
  );
}

function CodeBlock({ className, children, inline, ...props }: any) {
  const { t } = useT();
  const text = String(children ?? "").replace(/\n$/, "");
  const language = /language-(\w+)/.exec(className || "")?.[1] || "text";

  if (inline) {
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  }

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-[var(--line)] bg-[var(--code)] shadow-lift">
      <div className="flex h-9 items-center justify-between border-b border-[var(--line)] px-3 text-xs text-[var(--muted)]">
        <span>{language}</span>
        <button
          type="button"
          className="inline-flex h-7 w-7 items-center justify-center rounded-full hover:bg-white/10"
          title={t("Copy code")}
          onClick={() => navigator.clipboard.writeText(text)}
        >
          <Copy size={14} />
        </button>
      </div>
      <pre className="scrollbar p-3">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
    </div>
  );
}

function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="message-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{ code: CodeBlock }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

function humanizeToolName(value?: string) {
  if (!value) return "tool";
  if (value.startsWith("codex_mcp__") || value.startsWith("sammy_mcp__")) {
    const pluginName = value.split("__")[1];
    if (pluginName) return humanizeToolName(pluginName);
  }
  const functionPrefix = value.match(/^(.+?)_(list|get|send|search|find|create|update|read|draft|delete)_/i);
  const baseName = functionPrefix?.[1] || value;
  return baseName
    .replace(/^sammy_plugin__/, "")
    .replace(/^sammy_mcp__/, "")
    .replace(/^codex_plugin__/, "")
    .replace(/^codex_mcp__/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase())
    .trim() || value;
}

function toolEventDisplayName(event?: ToolEvent) {
  return event?.tool_display_name || humanizeToolName(event?.tool || event?.name);
}

function toolEventIcon(event?: ToolEvent): LucideIcon {
  if (event?.type === "memory") return Brain;
  const label = `${event?.tool_display_name ?? ""} ${event?.tool ?? ""} ${event?.name ?? ""}`.toLowerCase();
  if (label.includes("contact")) return ContactRound;
  if (label.includes("crm")) return BriefcaseBusiness;
  if (label.includes("mail") || label.includes("gmail") || label.includes("zoho")) return Mail;
  if (label.includes("github")) return Github;
  if (label.includes("search")) return Search;
  if (label.includes("file") || label.includes("folder")) return FolderOpen;
  return Wrench;
}

function toolEventGroupKey(event: ToolEvent) {
  return (event.tool || event.tool_display_name || toolEventDisplayName(event)).trim().toLowerCase();
}

function mergeConsecutiveToolEvents(events: ToolEvent[]) {
  const merged: ToolEvent[] = [];

  events.forEach((event) => {
    const previous = merged[merged.length - 1];
    const canMerge =
      previous &&
      previous.type === event.type &&
      (event.type === "start" || event.type === "result") &&
      toolEventGroupKey(previous) === toolEventGroupKey(event);

    if (canMerge) {
      if (event.type === "result") {
        previous.count = (previous.count ?? 1) + (event.count ?? 1);
      }
      return;
    }

    merged.push(event.type === "result" ? { ...event, count: event.count ?? 1 } : event);
  });

  return merged;
}

function visibleToolEvents(events: ToolEvent[], now = Date.now()) {
  const pendingByKey = new Map<string, Array<{ key: string; start?: ToolEvent; result?: ToolEvent; event?: ToolEvent }>>();
  const slots: Array<{ key: string; start?: ToolEvent; result?: ToolEvent; event?: ToolEvent }> = [];

  events.forEach((event) => {
    const key = toolEventGroupKey(event);

    if (event.type === "start") {
      const slot = { key, start: event };
      slots.push(slot);
      pendingByKey.set(key, [...(pendingByKey.get(key) ?? []), slot]);
      return;
    }

    if (event.type === "result") {
      const pending = pendingByKey.get(key) ?? [];
      const slot = pending.shift();
      pendingByKey.set(key, pending);
      if (slot) {
        slot.result = event;
      } else {
        slots.push({ key, result: event });
      }
      return;
    }

    slots.push({ key, event });
  });

  const visible = slots.flatMap((slot) => {
    if (slot.event) return [slot.event];
    if (slot.start && slot.result) {
      const startedAt = slot.start.received_at ?? slot.result.received_at ?? 0;
      return now - startedAt < MIN_TOOL_USING_MS ? [slot.start] : [{ ...slot.result, count: 1 }];
    }
    if (slot.start) return [slot.start];
    if (slot.result) return [{ ...slot.result, count: 1 }];
    return [];
  });

  return mergeConsecutiveToolEvents(visible);
}

function agentIcon(agent?: Agent): LucideIcon {
  const label = `${agent?.name ?? ""} ${(agent?.enabled_tools ?? []).join(" ")}`.toLowerCase();
  if (label.includes("mail") || label.includes("gmail") || label.includes("email") || label.includes("zoho")) return Mail;
  if (label.includes("crm") || label.includes("deal") || label.includes("sales")) return BriefcaseBusiness;
  if (label.includes("contact")) return ContactRound;
  if (label.includes("github") || label.includes("git")) return Github;
  if (label.includes("search") || label.includes("web")) return Search;
  if (label.includes("file") || label.includes("document") || label.includes("folder")) return FolderOpen;
  return Brain;
}

function agentIconUploadUrl(icon?: string) {
  if (!icon?.startsWith("upload:")) return "";
  return `${API_BASE}/api/files/${icon.slice("upload:".length)}`;
}

function resolveAgentLucideIcon(agent?: Agent): LucideIcon {
  const icon = agent?.icon?.trim() ?? "";
  if (icon && !icon.startsWith("upload:") && agentIconLibraryMap[icon]) {
    return agentIconLibraryMap[icon];
  }
  return agentIcon(agent);
}

function AgentAvatar({
  agent,
  iconSize = 17,
  className = "flex shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]",
}: {
  agent?: Agent;
  iconSize?: number;
  className?: string;
}) {
  const uploadUrl = agentIconUploadUrl(agent?.icon);
  if (uploadUrl) {
    return (
      <span className={[className, "overflow-hidden p-0"].join(" ")}>
        <img src={uploadUrl} alt="" className="h-full w-full object-cover" />
      </span>
    );
  }
  const Icon = resolveAgentLucideIcon(agent);
  return (
    <span className={className}>
      <Icon size={iconSize} />
    </span>
  );
}

function latestToolEvent(messages: Message[]) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const events = (messages[index].metadata?.tool_events ?? []) as ToolEvent[];
    if (events.length) return events[events.length - 1];
  }
  return undefined;
}

function splitProgressText(value: string) {
  return String(value || "")
    .replace(/\s+/g, " ")
    .replace(/:\s*(?=(?:Let|I|I'll|I’ll|First|Next|Now|The|This)\b)/g, ". ")
    .split(/(?<=[.!?])\s+(?=[A-Z"'])/)
    .map((item) => item.trim().replace(/[:;]\s*$/, "."))
    .filter(Boolean);
}

function compactProgressNotes(notes: string[]) {
  const seen = new Set<string>();
  const compact: string[] = [];
  notes.flatMap(splitProgressText).forEach((note) => {
    const normalized = note.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
    if (!normalized || seen.has(normalized)) return;
    seen.add(normalized);
    compact.push(note.length > 220 ? `${note.slice(0, 217).trim()}...` : note);
  });
  return compact.slice(-6);
}

function appendProgressNote(notes: any, content: string) {
  const current = Array.isArray(notes) ? notes.map(String) : [];
  return compactProgressNotes([...current, content]);
}

function ActivityTrace({
  notes,
  events,
  active,
}: {
  notes: string[];
  events: ToolEvent[];
  active: boolean;
}) {
  const { t } = useT();
  const [toolEventClock, setToolEventClock] = useState(0);
  const displayEvents = visibleToolEvents(events ?? []);
  const displayNotes = compactProgressNotes(notes ?? []);

  useEffect(() => {
    const now = Date.now();
    const nextTransitionAt = (events ?? [])
      .filter((event) => event.type === "start")
      .map((event) => (event.received_at ?? now) + MIN_TOOL_USING_MS)
      .filter((time) => time > now)
      .sort((a, b) => a - b)[0];

    if (!nextTransitionAt) return undefined;
    const timer = window.setTimeout(() => setToolEventClock((value) => value + 1), nextTransitionAt - now + 16);
    return () => window.clearTimeout(timer);
  }, [events, toolEventClock]);

  if (!displayNotes.length && !displayEvents.length) return null;

  return (
    <details
      className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 shadow-lift"
      open={active || !displayEvents.length}
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-[var(--ink)]">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent)]">
          <Lightbulb size={14} />
        </span>
        <span className="min-w-0 flex-1 truncate">{t("Thinking")}</span>
        {active ? (
          <span className="typing-dots shrink-0" aria-hidden="true">
            <span />
            <span />
            <span />
          </span>
        ) : null}
      </summary>

      <div className="mt-3 space-y-2 border-l border-[var(--line)] pl-3">
        {displayNotes.map((note, index) => (
          <div key={`${note}-${index}`} className="relative text-sm leading-6 text-[var(--muted)]">
            <span className="absolute -left-[1.05rem] top-2 h-2 w-2 rounded-full border border-[var(--surface)] bg-[var(--line-strong)]" />
            <span>{note}</span>
          </div>
        ))}

        {displayEvents.map((event, index) => {
          const displayName = toolEventDisplayName(event);
          const isRunning = event.type === "start";
          const isMemory = event.type === "memory";
          const count = event.count ?? 1;
          const Icon = toolEventIcon(event);
          return (
            <div key={`${event.name}-${index}`} className="relative flex min-w-0 items-center gap-2 rounded-lg bg-[var(--inset)] px-2.5 py-2 text-sm">
              <span className="absolute -left-[1.2rem] top-4 h-2 w-2 rounded-full border border-[var(--surface)] bg-[var(--accent)]" />
              <span
                className={[
                  "flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border",
                  isRunning || isMemory ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]" : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)]",
                ].join(" ")}
              >
                <Icon size={15} />
              </span>
              <span className="min-w-0 flex-1 truncate text-[var(--muted)]">
                {isMemory ? (
                  t('Saving "{file}" memory', { file: event.memory_file || t("memory") })
                ) : (
                  <>
                    {isRunning ? t("Using") : t("Used")}{" "}
                    <span className="font-semibold text-[var(--ink)]">{displayName}</span>
                    {!isRunning && count > 1 ? t(" {n} times", { n: count }) : ""}
                  </>
                )}
              </span>
            </div>
          );
        })}
      </div>
    </details>
  );
}

function ToolEventStrip({ events }: { events: ToolEvent[] }) {
  const { t } = useT();
  const [toolEventClock, setToolEventClock] = useState(0);
  const displayEvents = visibleToolEvents(events ?? []);

  useEffect(() => {
    const now = Date.now();
    const nextTransitionAt = (events ?? [])
      .filter((event) => event.type === "start")
      .map((event) => (event.received_at ?? now) + MIN_TOOL_USING_MS)
      .filter((time) => time > now)
      .sort((a, b) => a - b)[0];

    if (!nextTransitionAt) return undefined;
    const timer = window.setTimeout(() => setToolEventClock((value) => value + 1), nextTransitionAt - now + 16);
    return () => window.clearTimeout(timer);
  }, [events, toolEventClock]);

  if (!displayEvents.length) return null;
  return (
    <div className="mt-3 space-y-2">
      {displayEvents.map((event, index) => {
        const displayName = toolEventDisplayName(event);
        const isRunning = event.type === "start";
        const isMemory = event.type === "memory";
        const count = event.count ?? 1;
        const Icon = toolEventIcon(event);
        return (
          <div
            key={`${event.name}-${index}`}
            className={[
              "flex w-fit max-w-full items-center gap-2 rounded-lg border bg-[var(--surface)] px-2.5 py-1.5 text-sm",
              isRunning || isMemory ? "border-[var(--accent)] text-[var(--ink)]" : "border-[var(--line)] text-[var(--muted)]",
            ].join(" ")}
          >
            <span
              className={[
                "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border",
                isRunning || isMemory ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--green)]" : "border-[var(--line)] text-[var(--muted)]",
              ].join(" ")}
            >
              <Icon size={14} />
            </span>
            <span className="truncate">
              {isMemory ? (
                t('Saving "{file}" memory', { file: event.memory_file || t("memory") })
              ) : (
                <>
                  {isRunning ? t("Using") : t("Used")} <span className="font-medium text-[var(--ink)]">{displayName}</span>
                  {!isRunning && count > 1 ? t(" {n} times", { n: count }) : ""}
                </>
              )}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function GenerationLedger({ state }: { state: GenerationState }) {
  const { t } = useT();
  const counter = state.phase === "tool" && state.tool_step ? t("Step {n}", { n: state.tool_step }) : state.part > 1 ? t("Part {n}", { n: state.part }) : "";

  return (
    <div className="mx-auto w-full max-w-4xl px-3 sm:px-5" aria-live="polite">
      <div className="flex max-w-[min(760px,100%)] items-start gap-2.5 border-l-2 border-[var(--accent)] pl-3 text-sm">
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="font-medium text-[var(--ink)]">{t(state.label)}</span>
            {counter ? <span className="font-mono text-[0.68rem] uppercase text-[var(--muted)]">{counter}</span> : null}
            <span className="typing-dots ml-0.5 shrink-0" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
          </span>
          {state.detail ? <span className="mt-0.5 block text-xs leading-relaxed text-[var(--muted)]">{t(state.detail)}</span> : null}
        </span>
      </div>
    </div>
  );
}

function MessageBubble({
  message,
  onCopy,
  onRegenerate,
  activeActionMessageId,
  onActivateActions,
  generating,
}: {
  message: Message;
  onCopy: (content: string) => void;
  onRegenerate: (message: Message) => void;
  activeActionMessageId: string;
  onActivateActions: (messageId: string) => void;
  generating: boolean;
}) {
  const { t } = useT();
  const identity = useIdentity();
  if (message.role === "tool") {
    const title = message.metadata?.function_name || "tool";
    const isMemory = message.metadata?.tool_name === "sammy_memory";
    return (
      <div className="mx-auto w-full max-w-4xl px-3 sm:px-5">
        <ToolEventStrip
          events={[
            {
              type: isMemory ? "memory" : "result",
              name: title,
              tool: message.metadata?.tool_name,
              tool_display_name: message.metadata?.tool_display_name,
              memory_file: message.metadata?.memory_file,
              content: message.content,
            },
          ]}
        />
      </div>
    );
  }

  const isUser = message.role === "user";
  const toolEvents = (message.metadata?.tool_events ?? []) as ToolEvent[];
  const progressNotes = (message.metadata?.progress_notes ?? []) as string[];
  const reasoning = message.metadata?.reasoning as string | undefined;
  const userActionsVisible = activeActionMessageId === message.id;
  const isStreaming = message.metadata?.response_status === "streaming";

  if (isUser) {
    return (
      <div className="mx-auto w-full max-w-4xl px-3 sm:px-5">
        <div
          className="user-message-group flex flex-col items-end"
          data-actions-open={userActionsVisible ? "true" : undefined}
          onClick={() => {
            const preciseHover = window.matchMedia?.("(hover: hover) and (pointer: fine)").matches;
            if (!preciseHover || window.innerWidth < 768) onActivateActions(message.id);
          }}
        >
          <div className="max-w-[min(720px,88%)] rounded-xl border border-[var(--line)] bg-[var(--bubble-user)] px-4 py-2.5">
            <MarkdownMessage content={message.content || " "} />
          </div>
          <div className="user-message-actions flex justify-end gap-1 pr-1">
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
              title={t("Copy prompt")}
              aria-label={t("Copy prompt")}
              onClick={(event) => {
                event.stopPropagation();
                onCopy(message.content);
              }}
            >
              <Copy size={14} />
            </button>
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)] disabled:opacity-45"
              title={t("Regenerate from this message")}
              aria-label={t("Regenerate from this message")}
              disabled={generating}
              onClick={(event) => {
                event.stopPropagation();
                onRegenerate(message);
              }}
            >
              <RefreshCcw size={14} />
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-4xl px-3 sm:px-5">
      <div className="group max-w-[min(760px,100%)]">
        <div className="mb-2 flex items-center gap-2">
          <SammyLogo className="h-8 w-8 shrink-0" src={identity.avatarUrl} alt={identity.name} />
          <span className="text-sm font-medium text-[var(--ink)]">{identity.name}</span>
          <button
            type="button"
            className="flex h-7 w-7 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
            title={t("Copy message")}
            aria-label={t("Copy message")}
            onClick={() => onCopy(message.content)}
          >
            <Copy size={13} />
          </button>
        </div>
        <div className="pl-10">
          {message.content.trim() ? <MarkdownMessage content={message.content} /> : null}
          <ResponseReliabilityNotice message={message} />
          <ActivityTrace notes={progressNotes} events={toolEvents} active={isStreaming && generating} />
          {reasoning && (
            <details className="mt-3 rounded-lg border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-xs">
              <summary className="cursor-pointer text-[var(--muted)]">{t("Reasoning")}</summary>
              <pre className="scrollbar mt-2 max-h-52 overflow-auto whitespace-pre-wrap font-mono text-[0.72rem] text-[var(--muted)]">
                {reasoning}
              </pre>
            </details>
          )}
        </div>
      </div>
    </div>
  );
}

function EmptyState({ model }: { model: string }) {
  const { t } = useT();
  const identity = useIdentity();
  return (
    <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center px-6 text-center">
      <div className="mb-7 flex h-32 w-32 items-center justify-center sm:h-36 sm:w-36">
        <SammyLogo className="h-32 w-32 sm:h-36 sm:w-36" withShadow={false} src={identity.avatarUrl} alt={identity.name} />
      </div>
      <h1 className="font-display text-4xl font-semibold tracking-tight">{t("Hi, I'm {name}", { name: identity.name })}</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">{t("Your local companion, here whenever you need me. Everything stays between us.")}</p>
      <div className="mt-5 inline-flex w-fit items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 font-mono text-xs text-[var(--muted)]">
        <Circle size={8} className="fill-[var(--green)] text-[var(--green)]" />
        {model || t("No model selected")}
      </div>
    </div>
  );
}

const TOOL_BUILD_SPECIFICATIONS: Array<{
  title: string;
  icon: LucideIcon;
  items: string[];
}> = [
  {
    title: "What Sammy can build",
    icon: Code,
    items: [
      "Declarative MCP tools for HTTP APIs with 1 to 20 operations.",
      "GET, POST, PUT, PATCH, and DELETE operations with structured path, query, and body inputs.",
      "Public HTTPS services and explicitly approved private or local services.",
    ],
  },
  {
    title: "Authentication",
    icon: Database,
    items: [
      "No authentication, bearer tokens, API keys, and basic authentication are supported.",
      "Credentials are stored encrypted and never written into the generated tool.",
      "OAuth-only services require a manually reviewed adapter before Sammy can connect them.",
    ],
  },
  {
    title: "Safety boundaries",
    icon: ShieldCheck,
    items: [
      "Sammy verifies official API documentation and never invents endpoints or schemas.",
      "Each tool is locked to its approved host, access level, and network scope. Redirects outside that host are blocked.",
      "Generated tools cannot contain model-written executable code or overwrite an existing tool.",
    ],
  },
  {
    title: "Installation and use",
    icon: Wrench,
    items: [
      "Tools are validated, stored locally, and enabled automatically for the current agent.",
      "Public read-only tools can finish in one task. Write access and private networks require explicit approval.",
      "Tool calls use a 20 second timeout and responses are limited to 64 KB.",
    ],
  },
];

function ToolBuilderSpecifications() {
  const { t } = useT();
  return (
    <div className="grid border-y border-[var(--separator)] sm:grid-cols-2">
      {TOOL_BUILD_SPECIFICATIONS.map((section, index) => {
        const Icon = section.icon;
        return (
          <section
            key={section.title}
            className={[
              "py-5 sm:px-5",
              index % 2 === 0 ? "sm:border-r sm:border-[var(--separator)]" : "",
              index >= 2 ? "border-t border-[var(--separator)]" : index === 1 ? "border-t border-[var(--separator)] sm:border-t-0" : "",
            ].join(" ")}
          >
            <h3 className="flex items-center gap-2 text-sm font-semibold text-[var(--ink)]">
              <Icon size={16} className="text-[var(--accent)]" />
              {t(section.title)}
            </h3>
            <ul className="mt-3 space-y-2.5">
              {section.items.map((item) => (
                <li key={item} className="flex gap-2 text-sm leading-5 text-[var(--muted)]">
                  <Circle size={6} className="mt-2 shrink-0 fill-[var(--green)] text-[var(--green)]" />
                  <span>{t(item)}</span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function ToolBuilderIntro({ agentName, compact = false }: { agentName: string; compact?: boolean }) {
  const { t } = useT();
  const identity = useIdentity();
  if (compact) {
    return (
      <details className="mx-auto mb-6 w-full max-w-4xl border-y border-[var(--separator)] bg-[var(--canvas)] px-4 py-3 sm:px-6">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-sm font-semibold text-[var(--ink)]">
          <Wrench size={15} className="text-[var(--accent)]" />
          <span className="flex-1">{t("Tool Build Mode")}</span>
          <span className="text-xs font-normal text-[var(--muted)]">{t("View full specifications")}</span>
        </summary>
        <div className="mt-4">
          <ToolBuilderSpecifications />
        </div>
      </details>
    );
  }
  return (
    <div data-testid="tool-builder-intro" className="mx-auto w-full max-w-4xl px-5 py-8 sm:px-7 sm:py-10">
      <div className="flex items-start gap-4">
        <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--accent)]">
          <Wrench size={20} />
        </span>
        <div className="min-w-0">
          <p className="font-mono text-[0.68rem] uppercase text-[var(--accent)]">{t("Tool Build Mode")}</p>
          <h1 className="mt-1 font-display text-3xl font-semibold text-[var(--ink)] sm:text-4xl">{t("Build a tool with {name}", { name: identity.name })}</h1>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--muted)]">
            {t("Describe the service and what you want Sammy to do. Sammy will verify the API, generate a constrained local tool, and enable it for {agent}.", { agent: agentName })}
          </p>
        </div>
      </div>
      <div className="mt-7">
        <ToolBuilderSpecifications />
      </div>
      <div className="mt-6 border-l-2 border-[var(--accent)] pl-4">
        <h2 className="text-sm font-semibold text-[var(--ink)]">{t("What to describe")}</h2>
        <p className="mt-1 text-sm leading-5 text-[var(--muted)]">
          {t("Include the service name, what Sammy should do, and an official API or documentation link if you have one.")}
        </p>
      </div>
    </div>
  );
}

function LoginScreen({
  password,
  error,
  submitting,
  onPasswordChange,
  onSubmit,
}: {
  password: string;
  error: string;
  submitting: boolean;
  onPasswordChange: (value: string) => void;
  onSubmit: () => void;
}) {
  const { t } = useT();
  return (
    <div className="flex h-full items-center justify-center bg-[var(--canvas)] px-5 text-[var(--ink)]">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-popover">
        <div className="mb-5 flex items-center gap-3">
          <SammyLogo className="h-12 w-12" withShadow={false} />
          <div>
            <div className="font-display text-xl font-semibold tracking-tight">Sammy</div>
            <div className="text-sm text-[var(--muted)]">{t("Enter the desktop password")}</div>
          </div>
        </div>
        <form
          className="grid gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            onSubmit();
          }}
        >
          <label className="block text-sm">
            <span className="mb-1 block text-[var(--muted)]">{t("Password")}</span>
            <input
              type="password"
              className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
              value={password}
              onChange={(event) => onPasswordChange(event.target.value)}
              autoFocus
            />
          </label>
          {error ? <div className="text-sm text-[var(--red)]">{error}</div> : null}
          <button
            type="submit"
            className="rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--accent-ink)] disabled:opacity-50"
            disabled={!password || submitting}
          >
            {submitting ? t("Checking...") : t("Log in")}
          </button>
        </form>
      </div>
    </div>
  );
}

function AgentPickerModal({
  open,
  mode,
  agents,
  onClose,
  onSelect,
}: {
  open: boolean;
  mode: "new" | "send";
  agents: Agent[];
  onClose: () => void;
  onSelect: (agentId: string) => void;
}) {
  const { t } = useT();
  if (!open) return null;
  return (
    <>
      <button
        type="button"
        className="fixed inset-0 z-50 cursor-default bg-black/20"
        aria-label={t("Close agent picker")}
        onClick={onClose}
      />
      <div className="pointer-events-none fixed inset-0 z-50 flex items-center justify-center p-4">
        <div className="pointer-events-auto w-full max-w-sm rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-3 shadow-popover">
          <div className="flex items-center justify-between px-2 pb-2 pt-1">
            <div>
              <div className="text-sm font-semibold">{mode === "send" ? t("Send with agent") : t("New chat agent")}</div>
              <div className="text-xs text-[var(--muted)]">{t("{n} available", { n: agents.length })}</div>
            </div>
            <button
              type="button"
              className="flex h-8 w-8 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
              onClick={onClose}
              aria-label={t("Close")}
              title={t("Close")}
            >
              <X size={16} />
            </button>
          </div>
          <div className="grid gap-1">
            {agents.map((agent) => {
              const pluginCount = agent.enabled_tools.length;
              return (
                <button
                  key={agent.id}
                  type="button"
                  className="flex items-center gap-3 rounded-xl px-2.5 py-2.5 text-left hover:bg-[var(--surface-2)]"
                  onClick={() => onSelect(agent.id)}
                >
                  <AgentAvatar
                    agent={agent}
                    iconSize={17}
                    className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]"
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold">{agent.name}</span>
                    <span className="block truncate text-xs text-[var(--muted)]">
                      {pluginCount === 1 ? t("1 tool") : t("{n} tools", { n: pluginCount })}
                    </span>
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </div>
    </>
  );
}

function MemorySettings({
  settings,
  onUpdate,
  agents,
  setNotice,
}: {
  settings: SettingsShape;
  onUpdate: (patch: Partial<SettingsShape>) => void;
  agents: Agent[];
  setNotice: (notice: string) => void;
}) {
  const { t } = useT();
  const [data, setData] = useState<MemoryResponse>({
    memories: [],
    stats: { active: 0, pending: 0, archived: 0 },
  });
  const [filter, setFilter] = useState<"active" | "pending" | "archived">("active");
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState<"soul" | "user" | "agent">("user");
  const [agentId, setAgentId] = useState(agents[0]?.id || "default");
  const [content, setContent] = useState("");
  const [editingId, setEditingId] = useState("");
  const [editingContent, setEditingContent] = useState("");
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      setData(await api<MemoryResponse>("/api/memories"));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const addMemory = async () => {
    if (!content.trim()) return;
    await api<MemoryEntry>("/api/memories", {
      method: "POST",
      body: JSON.stringify({
        scope,
        agent_id: scope === "agent" ? agentId : "",
        kind: scope === "soul" ? "identity" : "fact",
        content: content.trim(),
        confidence: 1,
      }),
    });
    setContent("");
    setFilter("active");
    setNotice(t("Memory added locally"));
    await refresh();
  };

  const approve = async (id: string) => {
    await api(`/api/memories/${id}/approve`, { method: "POST" });
    setNotice(t("Memory approved"));
    await refresh();
  };

  const archive = async (id: string) => {
    await api(`/api/memories/${id}`, { method: "PATCH", body: JSON.stringify({ status: "archived" }) });
    setNotice(t("Memory archived"));
    await refresh();
  };

  const saveEdit = async (id: string) => {
    if (!editingContent.trim()) return;
    await api(`/api/memories/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ content: editingContent.trim() }),
    });
    setEditingId("");
    setEditingContent("");
    setNotice(t("Memory corrected"));
    await refresh();
  };

  const remove = async (id: string) => {
    await api(`/api/memories/${id}`, { method: "DELETE" });
    setNotice(t("Memory deleted"));
    await refresh();
  };

  const visible = data.memories.filter((entry) => {
    if (entry.status !== filter) return false;
    const needle = query.trim().toLowerCase();
    return !needle || `${entry.content} ${entry.kind} ${entry.source_label}`.toLowerCase().includes(needle);
  });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-[var(--line)] pb-4">
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]">
            <Database size={17} />
          </span>
          <div>
            <div className="text-sm font-semibold">{t("Local memory")}</div>
            <div className="mt-0.5 text-xs leading-relaxed text-[var(--muted)]">
              {t("Stored in SQLite on this Mac. Soul entries require manual edits.")}
            </div>
          </div>
        </div>
        <span className="font-mono text-[0.68rem] text-[var(--muted)]">{t("{n} active", { n: data.stats.active })}</span>
      </div>

      <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
        <div>
          <div className="mb-1.5 text-xs font-medium text-[var(--muted)]">{t("Post-turn review")}</div>
          <div className="flex w-fit rounded-lg bg-[var(--inset)] p-1">
            {(["auto", "ask", "off"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                className={`rounded-md px-3 py-1.5 text-xs font-medium capitalize ${
                  settings.memory_mode === mode
                    ? "border border-[var(--line)] bg-[var(--surface)] text-[var(--ink)]"
                    : "text-[var(--muted)]"
                }`}
                onClick={() => onUpdate({ memory_mode: mode })}
              >
                {t(mode)}
              </button>
            ))}
          </div>
        </div>
        <label className="flex items-center justify-between gap-4 text-sm sm:justify-end">
          <span>{t("Recall past chats")}</span>
          <span className="relative inline-flex items-center">
            <input
              type="checkbox"
              checked={settings.memory_recall_enabled}
              onChange={(event) => onUpdate({ memory_recall_enabled: event.target.checked })}
              className="peer sr-only"
            />
            <span className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full" />
          </span>
        </label>
      </div>

      <div className="border-y border-[var(--line)] py-4">
        <div className="mb-2 flex flex-wrap gap-2">
          <select
            value={scope}
            onChange={(event) => setScope(event.target.value as typeof scope)}
            className="rounded-lg border border-[var(--line)] bg-[var(--inset)] px-2.5 py-2 text-sm"
            title={t("Memory scope")}
          >
            <option value="user">{t("User")}</option>
            <option value="agent">{t("Agent")}</option>
            <option value="soul">{t("Soul")}</option>
          </select>
          {scope === "agent" ? (
            <select
              value={agentId}
              onChange={(event) => setAgentId(event.target.value)}
              className="min-w-36 rounded-lg border border-[var(--line)] bg-[var(--inset)] px-2.5 py-2 text-sm"
            >
              {agents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
            </select>
          ) : null}
        </div>
        <div className="flex gap-2">
          <input
            value={content}
            onChange={(event) => setContent(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void addMemory();
            }}
            placeholder={scope === "soul" ? t("Add a shared identity or behavior rule") : t("Add a durable fact or preference")}
            className="min-w-0 flex-1 rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 text-sm focus:border-[var(--accent)]"
          />
          <button
            type="button"
            onClick={() => void addMemory()}
            disabled={!content.trim()}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--accent)] text-[var(--accent-ink)] disabled:opacity-40"
            title={t("Add memory")}
            aria-label={t("Add memory")}
          >
            <Plus size={16} />
          </button>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex rounded-lg bg-[var(--inset)] p-1">
          {(["active", "pending", "archived"] as const).map((status) => (
            <button
              key={status}
              type="button"
              onClick={() => setFilter(status)}
              className={`rounded-md px-2.5 py-1.5 text-xs capitalize ${filter === status ? "bg-[var(--surface)] text-[var(--ink)]" : "text-[var(--muted)]"}`}
            >
              {t(status)} {data.stats[status]}
            </button>
          ))}
        </div>
        <label className="relative min-w-44 flex-1 sm:max-w-56">
          <Search size={14} className="absolute left-2.5 top-2.5 text-[var(--muted)]" />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t("Filter memory")}
            className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] py-2 pl-8 pr-2 text-xs"
          />
        </label>
      </div>

      <div className="divide-y divide-[var(--line)] border-y border-[var(--line)]">
        {loading ? <div className="py-8 text-center text-sm text-[var(--muted)]">{t("Loading memory...")}</div> : null}
        {!loading && !visible.length ? <div className="py-8 text-center text-sm text-[var(--muted)]">{t("No {filter} memories", { filter: t(filter) })}</div> : null}
        {visible.map((entry) => (
          <div key={entry.id} className="py-3.5">
            <div className="flex items-start gap-3">
              <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${entry.scope === "soul" ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--inset)] text-[var(--muted)]"}`}>
                {entry.scope === "soul" ? <ShieldCheck size={14} /> : <Brain size={14} />}
              </span>
              <div className="min-w-0 flex-1">
                {editingId === entry.id ? (
                  <div className="flex gap-2">
                    <input
                      value={editingContent}
                      onChange={(event) => setEditingContent(event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") void saveEdit(entry.id);
                        if (event.key === "Escape") setEditingId("");
                      }}
                      className="min-w-0 flex-1 rounded-lg border border-[var(--accent)] bg-[var(--inset)] px-2.5 py-1.5 text-sm"
                      aria-label={t("Edit memory")}
                    />
                    <button type="button" onClick={() => void saveEdit(entry.id)} className="rounded-full p-2 text-[var(--green)] hover:bg-[var(--surface-2)]" title={t("Save edit")} aria-label={t("Save edit")}><Check size={15} /></button>
                  </div>
                ) : (
                  <div className="text-sm leading-relaxed">{entry.content}</div>
                )}
                <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[0.66rem] text-[var(--muted)]">
                  <span>{entry.scope}{entry.scope === "agent" ? ` · ${agents.find((agent) => agent.id === entry.agent_id)?.name || entry.agent_id}` : ""}</span>
                  <span>{entry.kind}</span>
                  <span>{t("{n}% confidence", { n: Math.round(entry.confidence * 100) })}</span>
                  <span>{entry.source_conversation_title || entry.source_label || t("Local")}</span>
                  {entry.use_count ? <span>{t("recalled {n}x", { n: entry.use_count })}</span> : null}
                </div>
              </div>
              <div className="flex shrink-0 gap-1">
                {editingId !== entry.id ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEditingId(entry.id);
                      setEditingContent(entry.content);
                    }}
                    className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)]"
                    title={t("Edit memory")}
                    aria-label={t("Edit memory")}
                  >
                    <PenLine size={15} />
                  </button>
                ) : null}
                {entry.status === "pending" ? (
                  <button type="button" onClick={() => void approve(entry.id)} className="rounded-full p-2 text-[var(--green)] hover:bg-[var(--surface-2)]" title={t("Approve memory")} aria-label={t("Approve memory")}><Check size={15} /></button>
                ) : null}
                {entry.status !== "archived" ? (
                  <button type="button" onClick={() => void archive(entry.id)} className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)]" title={t("Archive memory")} aria-label={t("Archive memory")}><Archive size={15} /></button>
                ) : null}
                <button type="button" onClick={() => void remove(entry.id)} className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--red)]" title={t("Delete memory")} aria-label={t("Delete memory")}><Trash2 size={15} /></button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

type SettingsTab = "general" | "voice" | "agents" | "memory" | "tools" | "security" | "docs";
type SettingsFocusTarget = "gemini" | null;

// Turn bare URLs inside helper text into clickable links.
function linkifyText(text: string) {
  return text.split(/(https?:\/\/[^\s)]+)/g).map((part, index) =>
    /^https?:\/\//.test(part) ? (
      <a key={index} href={part} target="_blank" rel="noreferrer" className="text-[var(--accent)] underline underline-offset-2 break-all">
        {part}
      </a>
    ) : (
      <span key={index}>{part}</span>
    )
  );
}

// Best-known "get your API key here" pages, matched by substring on the tool's name.
const TOOL_KEY_URLS: Record<string, string> = {
  gmail: "https://console.cloud.google.com/apis/credentials",
  google_contacts: "https://console.cloud.google.com/apis/credentials",
  google: "https://console.cloud.google.com/apis/credentials",
  zoho: "https://api-console.zoho.com/",
  gemini: "https://aistudio.google.com/apikey",
  openai: "https://platform.openai.com/api-keys",
  anthropic: "https://console.anthropic.com/settings/keys",
  elevenlabs: "https://elevenlabs.io/app/settings/api-keys",
  picovoice: "https://console.picovoice.ai/",
  slack: "https://api.slack.com/apps",
  notion: "https://www.notion.so/my-integrations",
  github: "https://github.com/settings/tokens",
  stripe: "https://dashboard.stripe.com/apikeys",
  twilio: "https://console.twilio.com/",
  sendgrid: "https://app.sendgrid.com/settings/api_keys",
  airtable: "https://airtable.com/create/tokens",
  discord: "https://discord.com/developers/applications",
  telegram: "https://t.me/botfather",
  tavily: "https://app.tavily.com/home",
  serpapi: "https://serpapi.com/manage-api-key",
  openweather: "https://home.openweathermap.org/api_keys",
  weather: "https://home.openweathermap.org/api_keys",
  news: "https://newsapi.org/account",
};

// The page where a user generates/finds the API key(s) for a specific tool.
// Tries the curated map, then a URL mentioned in the tool's own field hints, then a web search.
function toolKeyUrl(tool: ToolInfo): string {
  const name = tool.name.toLowerCase();
  for (const [key, url] of Object.entries(TOOL_KEY_URLS)) {
    if (name.includes(key)) return url;
  }
  for (const field of tool.auth_fields) {
    const match = `${field.description ?? ""} ${field.placeholder ?? ""}`.match(/https?:\/\/[^\s)]+/);
    if (match) return match[0];
  }
  return `https://www.google.com/search?q=${encodeURIComponent(`${tool.display_name} API key`)}`;
}

// API-key links for the services Sammy itself uses (these live in Settings, not the tools list).
const SERVICE_KEY_LINKS: Array<{ name: string; where: string; url: string }> = [
  { name: "Gemini", where: "Settings → Security · turns photos into besties", url: "https://aistudio.google.com/apikey" },
  { name: "ElevenLabs", where: "Settings → Voice · premium cloud voices", url: "https://elevenlabs.io/app/settings/api-keys" },
  { name: "Picovoice", where: "Settings → Voice · “only my voice”", url: "https://console.picovoice.ai/" },
];

function DocsExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="font-medium text-[var(--accent)] underline underline-offset-2 break-all">
      {children}
    </a>
  );
}

function DocsCard({ icon: Icon, title, children }: { icon: LucideIcon; title: string; children: ReactNode }) {
  return (
    <section className="rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift">
      <h3 className="flex items-center gap-2 font-display text-base font-semibold tracking-tight">
        <span className="flex h-7 w-7 items-center justify-center rounded-lg bg-[var(--accent-soft)] text-[var(--accent)]">
          <Icon size={15} />
        </span>
        {title}
      </h3>
      <div className="mt-3 space-y-2.5 text-sm leading-relaxed text-[var(--ink)]">{children}</div>
    </section>
  );
}

function DocsCode({ children }: { children: ReactNode }) {
  return (
    <code className="block w-full overflow-x-auto rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 font-mono text-xs text-[var(--ink)]">
      {children}
    </code>
  );
}

function DocsSettings({ tools }: { tools: ToolInfo[] }) {
  const { t } = useT();
  // Tools that need credentials — surface their field hints (often "get a key at …").
  const keyTools = tools.filter(
    (tool) => tool.requires_auth || tool.auth_fields.some((field) => field.type === "password")
  );

  return (
    <div className="mx-auto max-w-2xl space-y-5">
      <p className="text-sm text-[var(--muted)]">
        {t("Setup guides and links for the things Sammy can use — installing on another computer, phone access, models, and API keys.")}
      </p>

      <DocsCard icon={Download} title={t("Install on another computer")}>
        <p className="text-[var(--muted)]">
          On another Mac, one command installs everything — Sammy, Ollama, and a default model — then launches it:
        </p>
        <DocsCode>curl -fsSL https://raw.githubusercontent.com/Depreck78/Sammy_AI/main/install.sh | bash</DocsCode>
        <p className="mt-2 text-[var(--muted)]">Already have the project folder? Run the installer inside it:</p>
        <DocsCode>./setup.sh</DocsCode>
        <p className="text-[var(--muted)]">
          It installs Ollama (via Homebrew on macOS), pulls a base model sized to that computer's RAM, builds the custom
          “sammy” model, and starts Sammy at http://localhost:3131. Choose a different base model with:
        </p>
        <DocsCode>SAMMY_MODEL=llama3.1:8b ./setup.sh</DocsCode>
        <p className="text-xs text-[var(--muted)]">
          Requires macOS with Homebrew. After the first run, start Sammy anytime with{" "}
          <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">sammy</code>.
        </p>
      </DocsCard>

      <DocsCard icon={Smartphone} title={t("Use Sammy on your phone")}>
        <p className="font-medium text-[var(--ink)]">{t("On the same Wi-Fi")}</p>
        <p className="text-[var(--muted)]">
          1. In Terminal, run <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">sammy lan</code> (or use the
          Phone access toggle in Settings → Security). 2. Set a login password. 3. On this Mac, tap Sammy's name in the
          top bar and open that link on your phone (e.g. <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">http://sammy.local:3131</code>).
        </p>
        <p className="mt-2 font-medium text-[var(--ink)]">{t("From any network (cellular / other Wi-Fi)")}</p>
        <p className="text-[var(--muted)]">
          Use Tailscale (a free, private VPN). Install it on this Mac and your phone, sign both into the same account, then
          run <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">sammy lan</code> and open the “From any
          network” link in that same popup.
        </p>
        <ul className="mt-1 list-disc space-y-1 pl-5 text-[var(--muted)]">
          <li>{t("Download Tailscale:")} <DocsExternalLink href="https://tailscale.com/download">tailscale.com/download</DocsExternalLink></li>
          <li>{t("iPhone app:")} <DocsExternalLink href="https://apps.apple.com/app/tailscale/id1470499037">App Store</DocsExternalLink> · {t("Android:")} <DocsExternalLink href="https://play.google.com/store/apps/details?id=com.tailscale.ipn">Google Play</DocsExternalLink></li>
        </ul>
        <p className="text-xs text-[var(--muted)]">{t("Keep this Mac awake and Ollama running while you connect.")}</p>
      </DocsCard>

      <DocsCard icon={Brain} title={t("Find & install models (Ollama)")}>
        <p className="text-[var(--muted)]">
          Browse every model in the Ollama library, then pull the one you want. After it downloads, pick it in
          Settings → General → Default model.
        </p>
        <p>{t("Browse models:")} <DocsExternalLink href="https://ollama.com/library">ollama.com/library</DocsExternalLink></p>
        <DocsCode>ollama pull llama3.1:8b</DocsCode>
        <DocsCode>ollama list</DocsCode>
        <DocsCode>ollama run llama3.1:8b</DocsCode>
        <DocsCode>ollama rm llama3.1:8b</DocsCode>
        <p className="text-xs text-[var(--muted)]">
          {t("Bigger models need more RAM. Sammy's default is the custom “sammy” model, sized to your Mac during install.")}
        </p>
      </DocsCard>

      <DocsCard icon={ShieldCheck} title={t("Where to get API keys")}>
        <p className="font-medium text-[var(--ink)]">{t("Sammy's built-in services")}</p>
        <ul className="space-y-1.5">
          {SERVICE_KEY_LINKS.map((service) => (
            <li key={service.name}>
              <DocsExternalLink href={service.url}>{service.name}</DocsExternalLink>
              <span className="text-xs text-[var(--muted)]"> — {service.where}</span>
            </li>
          ))}
        </ul>

        {keyTools.length ? (
          <>
            <p className="mt-3 font-medium text-[var(--ink)]">{t("Connected tools")}</p>
            <ul className="space-y-2.5">
              {keyTools.map((tool) => {
                const hints = tool.auth_fields
                  .filter((field) => field.name !== "access_token" && field.name !== "refresh_token")
                  .map((field) => field.description || field.placeholder || "")
                  .filter(Boolean);
                return (
                  <li key={tool.name}>
                    <span className="flex items-center gap-2">
                      <span
                        className="h-2 w-2 shrink-0 rounded-full"
                        style={{ backgroundColor: tool.plugin?.brand_color || "var(--muted)" }}
                      />
                      <span className="font-medium text-[var(--ink)]">{tool.display_name}</span>
                    </span>
                    {hints.length ? (
                      <span className="mt-0.5 block pl-4 text-xs leading-relaxed text-[var(--muted)]">
                        {hints.map((hint, index) => (
                          <span key={index} className="block">{linkifyText(hint)}</span>
                        ))}
                      </span>
                    ) : null}
                    <a
                      href={toolKeyUrl(tool)}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-0.5 block pl-4 text-xs font-medium text-[var(--accent)] underline underline-offset-2"
                    >
                      {t("Find my keys")} ↗
                    </a>
                  </li>
                );
              })}
            </ul>
          </>
        ) : null}
        <p className="text-xs text-[var(--muted)]">{t("Paste keys in the matching Settings tab — they're stored encrypted on your Mac.")}</p>
      </DocsCard>

      <DocsCard icon={Trash2} title={t("Uninstall Sammy")}>
        <p className="text-[var(--muted)]">
          Stops Sammy, removes the launch agent and the <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">sammy</code>{" "}
          commands, and asks before deleting your local data (chats, settings, encrypted keys in{" "}
          <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">~/.sammy</code>):
        </p>
        <DocsCode>sammy uninstall</DocsCode>
        <p className="text-[var(--muted)]">Skip the prompt by choosing what happens to your data:</p>
        <DocsCode>sammy uninstall --purge      # also delete all local data</DocsCode>
        <DocsCode>sammy uninstall --keep-data  # keep your data</DocsCode>
        <p className="text-xs text-[var(--muted)]">
          It leaves the project folder and Ollama + your models in place — remove those yourself if you want
          (e.g. <code className="rounded bg-[var(--inset)] px-1 font-mono text-xs">brew uninstall ollama</code>).
        </p>
      </DocsCard>
    </div>
  );
}

// In-app LAN/local switch. Flipping it restarts Sammy (via the backend, which spawns the CLI);
// sessions persist across the restart, so the user stays logged in. We poll until it's back.
function NetworkModeCard() {
  const { t } = useT();
  const [info, setInfo] = useState<NetworkInfo | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");

  useEffect(() => {
    let cancelled = false;
    void api<NetworkInfo>("/api/network")
      .then((data) => {
        if (!cancelled) setInfo(data);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  const setMode = async (lan: boolean) => {
    if (busy) return;
    setBusy(true);
    setNote("");
    try {
      await api("/api/network/mode", { method: "POST", body: JSON.stringify({ lan }) });
    } catch {
      /* the connection drops while Sammy restarts — expected */
    }
    for (let attempt = 0; attempt < 40; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 800));
      try {
        const next = await api<NetworkInfo>("/api/network");
        setInfo(next);
        if (next.lan_active === lan) break;
      } catch {
        /* still restarting */
      }
    }
    setBusy(false);
    setNote(lan ? t("Phone access is on.") : t("Phone access is off."));
  };

  const on = Boolean(info?.lan_active);
  return (
    <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift"].join(" ")}>
      <label className="flex items-center justify-between gap-4">
        <span className="min-w-0">
          <span className="block font-display text-base font-semibold tracking-tight">{t("Phone access")}</span>
          <span className="mt-0.5 block text-sm text-[var(--muted)]">
            {t("Let your phone reach Sammy on your network. Stays on across restarts.")}
          </span>
        </span>
        <span className="relative inline-flex shrink-0 items-center">
          <input
            type="checkbox"
            className="peer sr-only"
            checked={on}
            disabled={busy}
            onChange={(event) => void setMode(event.target.checked)}
          />
          <span className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full peer-disabled:opacity-50" />
        </span>
      </label>
      {busy ? (
        <p className="mt-3 text-xs text-[var(--muted)]">{t("Restarting Sammy… this takes a few seconds.")}</p>
      ) : note ? (
        <p className="mt-3 text-xs text-[var(--muted)]">{note}</p>
      ) : on ? (
        <p className="mt-3 text-xs text-[var(--muted)]">{t("On this Mac, tap Sammy's name in the top bar for the phone link.")}</p>
      ) : null}
    </section>
  );
}

function SettingsPanel({
  open,
  onClose,
  initialTab,
  initialFocusTarget,
  settings,
  setSettings,
  agents,
  setAgents,
  tools,
  refreshTools,
  models,
}: {
  open: boolean;
  onClose: () => void;
  initialTab: SettingsTab;
  initialFocusTarget: SettingsFocusTarget;
  settings: SettingsShape;
  setSettings: (settings: SettingsShape) => void;
  agents: Agent[];
  setAgents: (agents: Agent[]) => void;
  tools: ToolInfo[];
  refreshTools: () => Promise<void>;
  models: ModelInfo[];
}) {
  const { t, lang, setLang } = useT();
  const identity = useIdentity();
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [draftSettings, setDraftSettings] = useState(settings);
  const [editingAgentId, setEditingAgentId] = useState<string>("");
  const [agentDraft, setAgentDraft] = useState<Agent>({
    id: "",
    name: "",
    system_prompt: "",
    model: "",
    icon: "",
    enabled_tools: [],
  });
  const [iconUploading, setIconUploading] = useState(false);
  const [expandedTool, setExpandedTool] = useState<string>("");
  const [credentialDrafts, setCredentialDrafts] = useState<Record<string, Record<string, string>>>({});
  const [accessPasswordDraft, setAccessPasswordDraft] = useState("");
  const [clearAccessPassword, setClearAccessPassword] = useState(false);
  const [notice, setNotice] = useState("");
  const [pluginFilter, setPluginFilter] = useState<"all" | "allowed" | "not_allowed">("all");
  const ttsSupported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [voiceURI, setVoiceURI] = useState(() =>
    typeof window !== "undefined" ? window.localStorage.getItem(VOICE_URI_KEY) || "" : ""
  );
  const [voiceRate, setVoiceRate] = useState(() => {
    if (typeof window === "undefined") return 1;
    const stored = Number(window.localStorage.getItem(VOICE_RATE_KEY));
    return stored >= 0.5 && stored <= 1.5 ? stored : 1;
  });
  const [elKeyDraft, setElKeyDraft] = useState("");
  const [geminiKeyDraft, setGeminiKeyDraft] = useState("");
  const [elVoices, setElVoices] = useState<ElevenLabsVoice[]>([]);
  const [elVoicesError, setElVoicesError] = useState("");
  const [elVoicesLoading, setElVoicesLoading] = useState(false);
  const [enrolling, setEnrolling] = useState(false);
  const [enrollProgress, setEnrollProgress] = useState(0);
  const geminiSectionRef = useRef<HTMLElement | null>(null);
  const geminiInputRef = useRef<HTMLInputElement | null>(null);

  // Auto-save plumbing: most settings persist the instant they change, so there is
  // no global save button. Discrete controls (toggles, selects, pickers) save right
  // away; sliders and number fields debounce so we don't fire on every drag tick.
  const saveTimer = useRef<number | null>(null);
  const pendingPatch = useRef<Record<string, AnySettingValue>>({});

  const voicePrefix = voicePrefixFor(lang);
  const langVoices = voicesForLang(voices, voicePrefix);
  const premiumVoices = langVoices.filter(isPremiumVoice);
  const siriVoices = langVoices.filter((voice) => isSiriVoice(voice) && !isPremiumVoice(voice));
  const otherVoices = langVoices.filter((voice) => !isPremiumVoice(voice) && !isSiriVoice(voice));

  useEffect(() => {
    if (!ttsSupported) return;
    const synth = window.speechSynthesis;
    const loadVoices = () => {
      const all = synth.getVoices();
      setVoices(all);
      // Forget any saved voice that isn't a cute female voice in the current app language.
      setVoiceURI((current) => {
        if (!current) return current;
        const match = all.find((voice) => voice.voiceURI === current);
        if (match && !voiceMatchesLang(match, voicePrefix)) {
          window.localStorage.removeItem(VOICE_URI_KEY);
          return "";
        }
        return current;
      });
    };
    loadVoices();
    synth.addEventListener?.("voiceschanged", loadVoices);
    return () => synth.removeEventListener?.("voiceschanged", loadVoices);
  }, [ttsSupported, voicePrefix]);

  // Load the account's ElevenLabs voices (proxied through the backend, which holds the key).
  useEffect(() => {
    if (!open || !draftSettings.elevenlabs_enabled || !settings.elevenlabs_configured) return;
    let cancelled = false;
    setElVoicesLoading(true);
    setElVoicesError("");
    api<{ configured: boolean; voices: ElevenLabsVoice[] }>("/api/tts/voices")
      .then((data) => {
        if (!cancelled) setElVoices(data.voices || []);
      })
      .catch((error) => {
        if (!cancelled) setElVoicesError((error as Error).message);
      })
      .finally(() => {
        if (!cancelled) setElVoicesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [open, draftSettings.elevenlabs_enabled, settings.elevenlabs_configured]);

  const updateVoiceURI = (value: string) => {
    setVoiceURI(value);
    window.localStorage.setItem(VOICE_URI_KEY, value);
  };

  const updateVoiceRate = (value: number) => {
    setVoiceRate(value);
    window.localStorage.setItem(VOICE_RATE_KEY, String(value));
  };

  const refreshVoices = () => {
    if (ttsSupported) setVoices(window.speechSynthesis.getVoices());
  };

  const openVoiceSettings = async () => {
    try {
      await api("/api/voices/open-settings", { method: "POST" });
      setNotice(t("Opened macOS voice settings."));
    } catch (error) {
      setNotice((error as Error).message);
    }
  };

  const testVoice = () => {
    if (!ttsSupported) return;
    const synth = window.speechSynthesis;
    synth.cancel();
    const utterance = new SpeechSynthesisUtterance(t("Hi, I'm {name}. This is how I'll sound.", { name: identity.name }));
    const chosen = langVoices.find((voice) => voice.voiceURI === voiceURI) ?? pickDefaultCuteVoice(voices, voicePrefix);
    if (chosen) {
      utterance.voice = chosen;
      utterance.lang = chosen.lang;
    }
    utterance.rate = voiceRate;
    utterance.pitch = CUTE_PITCH;
    synth.speak(utterance);
  };

  const testElevenLabs = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/tts`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: t("Hi, I'm {name}. This is how I'll sound.", { name: identity.name }),
          voice_id: draftSettings.elevenlabs_voice_id,
        }),
      });
      if (!res.ok) {
        setNotice(await res.text());
        return;
      }
      const audio = new Audio(URL.createObjectURL(await res.blob()));
      void audio.play();
    } catch (error) {
      setNotice((error as Error).message);
    }
  };

  const enrollVoice = async () => {
    if (enrolling) return;
    setEnrolling(true);
    setEnrollProgress(0);
    try {
      const { enrollSpeaker } = await import("./voiceAuth");
      const profile = await enrollSpeaker(draftSettings.picovoice_access_key.trim(), (percent) =>
        setEnrollProgress(percent)
      );
      const next = await api<SettingsShape>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          picovoice_access_key: draftSettings.picovoice_access_key.trim(),
          picovoice_speaker_profile: profile,
        }),
      });
      setSettings(next);
      setNotice(t("Voice enrolled"));
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setEnrolling(false);
    }
  };

  useEffect(() => setDraftSettings(settings), [settings]);
  useEffect(() => {
    if (open) setTab(initialTab);
  }, [open, initialTab]);
  useEffect(() => {
    if (!open || initialFocusTarget !== "gemini") return;
    setTab("security");
    const timer = window.setTimeout(() => {
      geminiSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      geminiInputRef.current?.focus({ preventScroll: true });
    }, 80);
    return () => window.clearTimeout(timer);
  }, [open, initialFocusTarget]);
  useEffect(() => {
    setAccessPasswordDraft("");
    setClearAccessPassword(false);
  }, [settings.access_password_enabled, open]);

  useEffect(() => {
    if (!open) return;
    setCredentialDrafts((current) => {
      const next = credentialsFromTools(tools);
      tools.forEach((tool) => {
        tool.auth_fields
          .filter((field) => field.type === "password")
          .forEach((field) => {
            const typedValue = current[tool.name]?.[field.name];
            if (typedValue) {
              next[tool.name] = { ...(next[tool.name] ?? {}), [field.name]: typedValue };
            }
          });
      });
      return next;
    });
  }, [open, tools]);

  useEffect(() => {
    if (!open || !agents.length || agentDraft.id || agentDraft.name) return;
    const firstAgent = agents[0];
    setEditingAgentId(firstAgent.id);
    setAgentDraft({ ...firstAgent });
  }, [open, agents, agentDraft.id, agentDraft.name]);

  // Persist a partial change to the backend (which merges it) and confirm it.
  // Optimistic updates are applied by the callers before this runs.
  const persistSettings = async (patch: Record<string, AnySettingValue>) => {
    try {
      const next = await api<SettingsShape>("/api/settings", {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      setSettings(next);
      setNotice(t("Saved"));
    } catch (error) {
      // Roll the optimistic change back to the last confirmed settings.
      setSettings(settings);
      setDraftSettings(settings);
      setNotice((error as Error).message);
    }
  };

  // Discrete controls: apply live (so theme/model react instantly) and save now.
  const updateSetting = (patch: Partial<SettingsShape>) => {
    const next = { ...draftSettings, ...patch };
    setDraftSettings(next);
    setSettings(next);
    void persistSettings(patch);
  };

  // Continuous controls (sliders, number fields): stay responsive locally, then
  // save shortly after the user stops moving. Patches accumulate so a quick edit
  // to a second field doesn't drop the first.
  const updateSettingLater = (patch: Partial<SettingsShape>) => {
    setDraftSettings((current) => ({ ...current, ...patch }));
    pendingPatch.current = { ...pendingPatch.current, ...patch };
    if (saveTimer.current) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      const toSave = pendingPatch.current;
      pendingPatch.current = {};
      void persistSettings(toSave);
    }, 450);
  };

  // Secrets keep an explicit, scoped save — never auto-committed on each keystroke.
  const saveElevenLabsKey = async () => {
    if (!elKeyDraft.trim()) return;
    await persistSettings({ elevenlabs_api_key: elKeyDraft.trim() });
    setElKeyDraft("");
  };

  const saveGeminiKey = async () => {
    if (!geminiKeyDraft.trim()) return;
    await persistSettings({ gemini_api_key: geminiKeyDraft.trim() });
    setGeminiKeyDraft("");
  };

  const saveAccessPassword = async () => {
    if (clearAccessPassword) {
      await persistSettings({ clear_access_password: true });
    } else if (accessPasswordDraft) {
      await persistSettings({ access_password: accessPasswordDraft });
    } else {
      return;
    }
    setAccessPasswordDraft("");
    setClearAccessPassword(false);
  };

  const beginAgent = (agent?: Agent) => {
    if (agent) {
      setEditingAgentId(agent.id);
      setAgentDraft({ ...agent });
    } else {
      setEditingAgentId("");
      setAgentDraft({ id: "", name: "", system_prompt: "", model: "", icon: "", enabled_tools: [] });
    }
    setTab("agents");
  };

  const saveAgent = async () => {
    const payload = {
      name: agentDraft.name || "New Agent",
      system_prompt: agentDraft.system_prompt,
      model: agentDraft.model,
      icon: agentDraft.icon || "",
      enabled_tools: agentDraft.enabled_tools,
    };
    if (editingAgentId) {
      await api<Agent>(`/api/agents/${editingAgentId}`, { method: "PUT", body: JSON.stringify(payload) });
    } else {
      await api<Agent>("/api/agents", { method: "POST", body: JSON.stringify(payload) });
    }
    const data = await api<{ agents: Agent[] }>("/api/agents");
    setAgents(data.agents);
    setNotice(t("Saved"));
  };

  const deleteAgent = async (agentId: string) => {
    await api(`/api/agents/${agentId}`, { method: "DELETE" });
    const data = await api<{ agents: Agent[] }>("/api/agents");
    setAgents(data.agents);
  };

  const persistToolCredentials = async (tool: ToolInfo) => {
    const draft = credentialDrafts[tool.name] ?? {};
    const credentials = Object.fromEntries(
      tool.auth_fields
        .filter((field) => field.name !== "access_token" && field.name !== "refresh_token")
        .map((field) => [field.name, draft[field.name] ?? ""])
    );
    await api(`/api/plugins/${tool.name}/credentials`, {
      method: "PUT",
      body: JSON.stringify({ credentials }),
    });
    setCredentialDrafts((current) => {
      const nextDraft = { ...(current[tool.name] ?? {}) };
      tool.auth_fields
        .filter((field) => field.type === "password")
        .forEach((field) => {
          delete nextDraft[field.name];
        });
      return { ...current, [tool.name]: nextDraft };
    });
  };

  const saveToolCredentials = async (tool: ToolInfo) => {
    await persistToolCredentials(tool);
    await refreshTools();
    setNotice(`${tool.display_name} credentials saved`);
  };

  const connectOAuth = (tool: ToolInfo) => {
    const popup = window.open("", "_blank");
    if (!popup) {
      setNotice(`Popup blocked. Allow popups to connect ${tool.display_name}.`);
      return;
    }

    void persistToolCredentials(tool)
      .then(() => {
        popup.location.href = `${API_BASE}/api/plugins/${tool.name}/oauth/start`;
        setNotice(`Connecting ${tool.display_name}...`);
        const timer = window.setInterval(() => {
          if (!popup.closed) return;
          window.clearInterval(timer);
          void refreshTools().then(() => setNotice(`${tool.display_name} OAuth status updated`));
        }, 800);
      })
      .catch((error) => {
        popup.close();
        setNotice(`Could not connect ${tool.display_name}. ${(error as Error).message}`);
      });
  };

  const toggleAgentTool = (toolName: string) => {
    const enabled = new Set(agentDraft.enabled_tools);
    if (enabled.has(toolName)) enabled.delete(toolName);
    else enabled.add(toolName);
    setAgentDraft({ ...agentDraft, enabled_tools: Array.from(enabled) });
  };

  const uploadAgentIcon = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setNotice(t("Choose an image file"));
      return;
    }
    setIconUploading(true);
    try {
      const body = new FormData();
      body.append("file", file);
      const response = await fetch(`${API_BASE}/api/files/upload`, { method: "POST", credentials: "include", body });
      if (!response.ok) {
        setNotice(await response.text());
        return;
      }
      const uploaded = await response.json();
      setAgentDraft({ ...agentDraft, icon: `upload:${uploaded.id}` });
      setNotice(t("Icon uploaded"));
    } finally {
      setIconUploading(false);
    }
  };

  return (
    <>
      {open ? (
        <button
          type="button"
          className="settings-scrim fixed inset-0 z-30 cursor-default bg-[color:rgba(44,33,27,0.3)] backdrop-blur-[2px]"
          aria-label={t("Close settings")}
          onClick={onClose}
        />
      ) : null}
      <div
        className={[
          "settings-panel fixed inset-y-0 left-0 z-40 flex w-full max-w-[880px] flex-col border-r border-[var(--line)] bg-[var(--panel)] shadow-popover",
          open ? "translate-x-0" : "-translate-x-full",
        ].join(" ")}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-[var(--line)] px-5">
          <div className="flex items-center gap-2.5">
            <span className="flex h-9 w-9 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[var(--accent)]">
              <Settings size={18} />
            </span>
            <div className="leading-tight">
              <div className="font-display text-lg font-semibold tracking-tight">{t("Settings")}</div>
              <div className="text-xs text-[var(--muted)]">{t("Tune how Sammy works")}</div>
            </div>
          </div>
          <button
            type="button"
            className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
            onClick={onClose}
            title={t("Close settings")}
            aria-label={t("Close settings")}
          >
            <X size={18} />
          </button>
        </div>

        <div className="flex min-h-0 flex-1">
          <nav className="flex w-16 shrink-0 flex-col gap-1 border-r border-[var(--line)] bg-[var(--side)] p-2 sm:w-[216px] sm:p-3">
            {([
              { id: "general", label: "General", caption: "Model, appearance, language", Icon: SlidersHorizontal },
              { id: "voice", label: "Voice", caption: "Speech, voices, and identity", Icon: Volume2 },
              { id: "agents", label: "Agents", caption: "Personas and their tools", Icon: Bot },
              { id: "memory", label: "Memory", caption: "What Sammy remembers", Icon: Brain },
              { id: "tools", label: "Tools", caption: "Connected apps and keys", Icon: Wrench },
              { id: "security", label: "Security", caption: "Password & API keys", Icon: ShieldCheck },
              { id: "docs", label: "Docs", caption: "Setup guides & key links", Icon: GraduationCap },
            ] as const).map(({ id, label, caption, Icon }) => {
              const active = tab === id;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  title={t(label)}
                  className={[
                    "nav-pill group flex items-center justify-center gap-3 rounded-xl px-2 py-2 text-left sm:justify-start sm:px-2.5",
                    id === "docs" ? "mt-auto" : "",
                    active
                      ? "bg-[var(--surface)] text-[var(--ink)] shadow-lift"
                      : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
                  ].join(" ")}
                >
                  <span
                    className={[
                      "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                      active
                        ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                        : "text-[var(--muted)] group-hover:text-[var(--ink)]",
                    ].join(" ")}
                  >
                    <Icon size={17} />
                  </span>
                  <span className="hidden min-w-0 sm:block">
                    <span className="block text-sm font-semibold">{t(label)}</span>
                    <span className="block truncate text-[0.7rem] font-normal text-[var(--muted)]">{t(caption)}</span>
                  </span>
                </button>
              );
            })}
          </nav>

          <div className="flex min-w-0 flex-1 flex-col">
            <div className="scrollbar flex-1 overflow-y-auto p-5 sm:p-6">
          {(tab === "general" || tab === "voice" || tab === "security") && (
            <div className="mx-auto max-w-2xl space-y-5">
              {tab === "security" ? <NetworkModeCard /> : null}
              <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "general" ? "" : "hidden"].join(" ")}>
                <h3 className="font-display text-base font-semibold tracking-tight">{t("Model & responses")}</h3>
                <p className="mt-0.5 text-sm text-[var(--muted)]">{t("Choose a default model and shape how Sammy replies.")}</p>
                <div className="mt-4 space-y-4">
                  <label className="block text-sm">
                    <span className="mb-1.5 block font-medium text-[var(--ink)]">{t("Default model")}</span>
                    <select
                      className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                      value={draftSettings.default_model}
                      onChange={(event) => updateSetting({ default_model: event.target.value })}
                    >
                      <option value="">{t("Auto select")}</option>
                      {models.map((model) => (
                        <option value={model.name} key={model.name}>
                          {formatModel(model)}
                        </option>
                      ))}
                    </select>
                    <span className="mt-1.5 block text-xs text-[var(--muted)]">{t("New chats use this unless an agent sets its own model.")}</span>
                  </label>

                  <label className="block text-sm">
                    <span className="mb-2 flex items-center justify-between font-medium text-[var(--ink)]">
                      <span>{t("Context window")}</span>
                      <span className="font-mono text-xs text-[var(--accent)]">{t("{n} tokens", { n: draftSettings.num_ctx.toLocaleString() })}</span>
                    </span>
                    <input
                      type="range"
                      min={2048}
                      max={32768}
                      step={1024}
                      value={draftSettings.num_ctx}
                      onChange={(event) => updateSettingLater({ num_ctx: Number(event.target.value) })}
                      className="w-full accent-[var(--accent)]"
                    />
                    <span className="mt-1.5 block text-xs text-[var(--muted)]">{t("How much conversation Sammy keeps in mind at once.")}</span>
                  </label>

                  <div className="grid grid-cols-2 gap-3">
                    <label className="block text-sm">
                      <span className="mb-1.5 block font-medium text-[var(--ink)]">{t("Max tokens")}</span>
                      <input
                        type="number"
                        min={64}
                        max={8192}
                        className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                        value={draftSettings.num_predict}
                        onChange={(event) => updateSettingLater({ num_predict: Number(event.target.value) })}
                      />
                      <span className="mt-1.5 block text-xs text-[var(--muted)]">{t("Longest reply Sammy will write.")}</span>
                    </label>
                    <label className="block text-sm">
                      <span className="mb-1.5 block font-medium text-[var(--ink)]">{t("Temperature")}</span>
                      <input
                        type="number"
                        min={0}
                        max={1}
                        step={0.05}
                        className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                        value={draftSettings.temperature}
                        onChange={(event) => updateSettingLater({ temperature: Number(event.target.value) })}
                      />
                      <span className="mt-1.5 block text-xs text-[var(--muted)]">{t("Lower is focused, higher is playful.")}</span>
                    </label>
                  </div>
                </div>
              </section>

              <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "general" ? "" : "hidden"].join(" ")}>
                <h3 className="font-display text-base font-semibold tracking-tight">{t("Appearance")}</h3>
                <p className="mt-0.5 text-sm text-[var(--muted)]">{t("Pick the mood that suits your room.")}</p>
                <div className="mt-4 grid grid-cols-2 gap-2.5">
                  {([
                    { value: "light", label: "Light", hint: "Warm daylight" },
                    { value: "dark", label: "Dark", hint: "Warm and dim" },
                  ] as const).map((option) => {
                    const active = draftSettings.theme === option.value;
                    return (
                      <button
                        key={option.value}
                        type="button"
                        onClick={() => updateSetting({ theme: option.value })}
                        className={[
                          "rounded-xl border px-4 py-3 text-left",
                          active
                            ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                            : "border-[var(--line)] bg-[var(--inset)] hover:border-[var(--line-strong)]",
                        ].join(" ")}
                      >
                        <span className="flex items-center justify-between">
                          <span className="text-sm font-semibold text-[var(--ink)]">{t(option.label)}</span>
                          {active ? <Check size={16} className="text-[var(--accent)]" /> : null}
                        </span>
                        <span className="mt-0.5 block text-xs text-[var(--muted)]">{t(option.hint)}</span>
                      </button>
                    );
                  })}
                </div>
              </section>

              <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "general" ? "" : "hidden"].join(" ")}>
                <h3 className="font-display text-base font-semibold tracking-tight">{t("Language")}</h3>
                <p className="mt-0.5 text-sm text-[var(--muted)]">{t("Choose the language Sammy speaks and shows.")}</p>
                <div className="mt-4 grid grid-cols-3 gap-2.5">
                  {LANGUAGES.map((option) => {
                    const active = lang === option.code;
                    return (
                      <button
                        key={option.code}
                        type="button"
                        onClick={() => setLang(option.code)}
                        className={[
                          "rounded-xl border px-3 py-3 text-left",
                          active
                            ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                            : "border-[var(--line)] bg-[var(--inset)] hover:border-[var(--line-strong)]",
                        ].join(" ")}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="truncate text-sm font-semibold text-[var(--ink)]">{option.nativeLabel}</span>
                          {active ? <Check size={16} className="shrink-0 text-[var(--accent)]" /> : null}
                        </span>
                        <span className="mt-0.5 block truncate text-xs text-[var(--muted)]">{t(option.label)}</span>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-3 text-xs text-[var(--muted)]">{t("The voice list adapts to this language.")}</p>
              </section>

              <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "voice" ? "" : "hidden"].join(" ")}>
                <h3 className="font-display text-base font-semibold tracking-tight">{t("Voice")}</h3>
                <p className="mt-0.5 text-sm text-[var(--muted)]">
                  {t("How Sammy sounds when reading replies aloud in hands-free mode.")}
                </p>

                <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--inset)] p-4">
                  <label className="flex items-center justify-between gap-4">
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-[var(--ink)]">{t("Use ElevenLabs voices")}</span>
                      <span className="mt-0.5 block text-xs text-[var(--muted)]">{t("Premium cloud voices via your ElevenLabs account.")}</span>
                    </span>
                    <span className="relative inline-flex shrink-0 items-center">
                      <input
                        type="checkbox"
                        checked={draftSettings.elevenlabs_enabled}
                        onChange={(event) => updateSetting({ elevenlabs_enabled: event.target.checked })}
                        className="peer sr-only"
                      />
                      <span className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full" />
                    </span>
                  </label>

                  <div className="mt-3 space-y-3">
                    <label className="block text-sm">
                      <span className="mb-1 flex items-center justify-between gap-2 font-medium text-[var(--ink)]">
                        <span>{t("API key")}</span>
                        <span
                          className={[
                            "shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] font-medium",
                            settings.elevenlabs_configured ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--surface-2)] text-[var(--muted)]",
                          ].join(" ")}
                        >
                          {settings.elevenlabs_configured ? t("Connected") : t("Not connected")}
                        </span>
                      </span>
                      <input
                        type="password"
                        autoComplete="off"
                        className="w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 focus:border-[var(--accent)]"
                        placeholder={settings.elevenlabs_configured ? t("Connected — leave blank to keep current key") : t("Paste your ElevenLabs API key")}
                        value={elKeyDraft}
                        onChange={(event) => setElKeyDraft(event.target.value)}
                      />
                      <span className="mt-1.5 block text-xs text-[var(--muted)]">
                        {t("The key stays on your Mac, used only by the local Sammy backend — it never reaches the browser.")}
                      </span>
                    </label>

                    {elKeyDraft.trim() ? (
                      <button
                        type="button"
                        className="inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--accent-ink)] hover:brightness-105"
                        onClick={() => void saveElevenLabsKey()}
                      >
                        <Check size={15} />
                        {t("Save key")}
                      </button>
                    ) : null}

                    {draftSettings.elevenlabs_enabled && settings.elevenlabs_configured ? (
                      <label className="block text-sm">
                        <span className="mb-1.5 block font-medium text-[var(--ink)]">{t("Voice")}</span>
                        {elVoices.length > 0 ? (
                          (() => {
                            const female = elVoices.filter((voice) => (voice.labels?.gender || "").toLowerCase() === "female");
                            const shown = female.length ? female : elVoices;
                            return (
                              <select
                                className="w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 focus:border-[var(--accent)]"
                                value={draftSettings.elevenlabs_voice_id}
                                onChange={(event) => updateSetting({ elevenlabs_voice_id: event.target.value })}
                              >
                                <option value="">{t("Default voice")}</option>
                                {shown.map((voice) => (
                                  <option key={voice.voice_id} value={voice.voice_id}>
                                    {voice.name}
                                    {voice.labels?.accent ? ` · ${voice.labels.accent}` : ""}
                                  </option>
                                ))}
                              </select>
                            );
                          })()
                        ) : (
                          <input
                            type="text"
                            className="w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 focus:border-[var(--accent)]"
                            placeholder={t("Paste a voice ID")}
                            value={draftSettings.elevenlabs_voice_id}
                            onChange={(event) => updateSettingLater({ elevenlabs_voice_id: event.target.value })}
                          />
                        )}
                        <span className="mt-1.5 block text-xs text-[var(--muted)]">
                          {elVoicesLoading
                            ? t("Loading voices…")
                            : elVoicesError
                              ? t("Couldn't list your voices — paste a voice ID from your ElevenLabs dashboard instead.")
                              : elVoices.length === 0
                                ? t("No voices found in your ElevenLabs account.")
                                : ""}
                        </span>
                        <button
                          type="button"
                          className="mt-2 inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-4 py-2 text-sm font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]"
                          onClick={testElevenLabs}
                        >
                          <Volume2 size={15} />
                          {t("Test voice")}
                        </button>
                      </label>
                    ) : null}
                  </div>
                </div>

                <div className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--inset)] p-4">
                  <label className="flex items-center justify-between gap-4">
                    <span className="min-w-0">
                      <span className="block text-sm font-semibold text-[var(--ink)]">{t("Only respond to my voice")}</span>
                      <span className="mt-0.5 block text-xs text-[var(--muted)]">
                        {t("On-device speaker check (Picovoice Eagle) — ignores other people's voices.")}
                      </span>
                    </span>
                    <span className="relative inline-flex shrink-0 items-center">
                      <input
                        type="checkbox"
                        checked={draftSettings.voice_auth_enabled}
                        onChange={(event) => updateSetting({ voice_auth_enabled: event.target.checked })}
                        className="peer sr-only"
                      />
                      <span className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full" />
                    </span>
                  </label>

                  <div className="mt-3 space-y-3">
                    <label className="block text-sm">
                      <span className="mb-1 flex items-center justify-between gap-2 font-medium text-[var(--ink)]">
                        <span>{t("Picovoice AccessKey")}</span>
                        <span
                          className={[
                            "shrink-0 rounded-full px-2 py-0.5 text-[0.62rem] font-medium",
                            settings.picovoice_speaker_profile ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--surface-2)] text-[var(--muted)]",
                          ].join(" ")}
                        >
                          {settings.picovoice_speaker_profile ? t("Voice enrolled") : t("Not enrolled")}
                        </span>
                      </span>
                      <input
                        type="password"
                        autoComplete="off"
                        className="w-full rounded-xl border border-[var(--line)] bg-[var(--surface)] px-3 py-2.5 focus:border-[var(--accent)]"
                        placeholder={t("Paste your free Picovoice AccessKey")}
                        value={draftSettings.picovoice_access_key}
                        onChange={(event) => setDraftSettings({ ...draftSettings, picovoice_access_key: event.target.value })}
                      />
                      <span className="mt-1.5 block text-xs text-[var(--muted)]">
                        {t("Runs fully on-device. Get a free key at console.picovoice.ai.")}
                      </span>
                    </label>

                    <div className="flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        disabled={enrolling || !draftSettings.picovoice_access_key.trim() || !voiceAuthSupported()}
                        className="inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--accent-ink)] disabled:opacity-50"
                        onClick={() => void enrollVoice()}
                      >
                        <Mic size={15} />
                        {enrolling
                          ? t("Enrolling… {n}%", { n: enrollProgress })
                          : settings.picovoice_speaker_profile
                            ? t("Re-enroll my voice")
                            : t("Enroll my voice")}
                      </button>
                      <span className="text-xs text-[var(--muted)]">{t("Speak naturally for ~20 seconds.")}</span>
                    </div>
                  </div>
                </div>

                {ttsSupported ? (
                  <div className="mt-4 space-y-4">
                    <label className="block text-sm">
                      <span className="mb-1.5 block font-medium text-[var(--ink)]">{t("Voice")}</span>
                      <select
                        className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                        value={voiceURI}
                        onChange={(event) => updateVoiceURI(event.target.value)}
                      >
                        <option value="">{t("Automatic (best available)")}</option>
                        {premiumVoices.length ? (
                          <optgroup label={t("Premium")}>
                            {premiumVoices.map((voice) => (
                              <option key={voice.voiceURI} value={voice.voiceURI}>{voice.name} ({voice.lang})</option>
                            ))}
                          </optgroup>
                        ) : null}
                        {siriVoices.length ? (
                          <optgroup label={t("Siri")}>
                            {siriVoices.map((voice) => (
                              <option key={voice.voiceURI} value={voice.voiceURI}>{voice.name} ({voice.lang})</option>
                            ))}
                          </optgroup>
                        ) : null}
                        {otherVoices.length ? (
                          <optgroup label={t("Other voices")}>
                            {otherVoices.map((voice) => (
                              <option key={voice.voiceURI} value={voice.voiceURI}>{voice.name} ({voice.lang})</option>
                            ))}
                          </optgroup>
                        ) : null}
                      </select>
                      <span className="mt-1.5 block text-xs text-[var(--muted)]">
                        {t("Premium and Siri voices are listed first — they sound the most natural.")}
                      </span>
                      {premiumVoices.length === 0 ? (
                        <div className="mt-2 rounded-xl border border-[var(--line)] bg-[var(--inset)] p-3">
                          <p className="text-xs leading-relaxed text-[var(--muted)]">
                            {t("Want a more natural voice? Premium voices are free from macOS — download one, then tap Refresh.")}
                          </p>
                          <div className="mt-2.5 flex flex-wrap gap-2">
                            <button
                              type="button"
                              onClick={() => void openVoiceSettings()}
                              className="rounded-full bg-[var(--accent)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-ink)] hover:brightness-105"
                            >
                              {t("Open voice settings")}
                            </button>
                            <button
                              type="button"
                              onClick={refreshVoices}
                              className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]"
                            >
                              {t("Refresh")}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </label>

                    <label className="block text-sm">
                      <span className="mb-2 flex items-center justify-between font-medium text-[var(--ink)]">
                        <span>{t("Speaking speed")}</span>
                        <span className="font-mono text-xs text-[var(--accent)]">{voiceRate.toFixed(2)}×</span>
                      </span>
                      <input
                        type="range"
                        min={0.5}
                        max={1.5}
                        step={0.05}
                        value={voiceRate}
                        onChange={(event) => updateVoiceRate(Number(event.target.value))}
                        className="w-full accent-[var(--accent)]"
                      />
                    </label>

                    <button
                      type="button"
                      className="inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--inset)] px-4 py-2 text-sm font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]"
                      onClick={testVoice}
                    >
                      <Volume2 size={15} />
                      {t("Test voice")}
                    </button>
                  </div>
                ) : (
                  <p className="mt-4 rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-3 text-sm text-[var(--muted)]">
                    {t("Spoken replies aren't supported in this browser.")}
                  </p>
                )}
              </section>

              <section
                ref={geminiSectionRef}
                className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "security" ? "" : "hidden"].join(" ")}
              >
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="font-display text-base font-semibold tracking-tight">{t("Gemini")}</h3>
                    <p className="mt-0.5 text-sm text-[var(--muted)]">{t("Restyle your photos into besties. The key stays on your Mac.")}</p>
                  </div>
                  <span
                    className={[
                      "shrink-0 rounded-full px-2.5 py-1 text-xs font-medium",
                      settings.gemini_configured ? "bg-[var(--accent-soft)] text-[var(--accent)]" : "bg-[var(--surface-2)] text-[var(--muted)]",
                    ].join(" ")}
                  >
                    {settings.gemini_configured ? t("Connected") : t("Not connected")}
                  </span>
                </div>
                <input
                  ref={geminiInputRef}
                  type="password"
                  autoComplete="off"
                  className="mt-4 w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                  placeholder={settings.gemini_configured ? t("Connected — leave blank to keep current key") : t("Paste your Gemini API key")}
                  value={geminiKeyDraft}
                  onChange={(event) => setGeminiKeyDraft(event.target.value)}
                />
                <span className="mt-1.5 block text-xs text-[var(--muted)]">{t("Get a free key at aistudio.google.com.")}</span>
                {geminiKeyDraft.trim() ? (
                  <button
                    type="button"
                    className="mt-3 inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--accent-ink)] hover:brightness-105"
                    onClick={() => void saveGeminiKey()}
                  >
                    <Check size={15} />
                    {t("Save key")}
                  </button>
                ) : null}
              </section>

              <section className={["rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift", tab === "security" ? "" : "hidden"].join(" ")}>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <h3 className="font-display text-base font-semibold tracking-tight">{t("Login password")}</h3>
                    <p className="mt-0.5 text-sm text-[var(--muted)]">{t("Ask for a password before opening Sammy.")}</p>
                  </div>
                  <span
                    className={[
                      "shrink-0 rounded-full px-2.5 py-1 text-xs font-medium",
                      settings.access_password_enabled
                        ? "bg-[var(--accent-soft)] text-[var(--accent)]"
                        : "bg-[var(--surface-2)] text-[var(--muted)]",
                    ].join(" ")}
                  >
                    {settings.access_password_enabled ? t("On") : t("Off")}
                  </span>
                </div>
                <input
                  type="password"
                  className="mt-4 w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                  placeholder={settings.access_password_enabled ? t("Leave blank to keep current password") : t("Set a password")}
                  value={accessPasswordDraft}
                  onChange={(event) => setAccessPasswordDraft(event.target.value)}
                />
                <div className="mt-4">
                  <button
                    type="button"
                    disabled={!accessPasswordDraft}
                    className="rounded-full bg-[var(--accent)] px-4 py-2 text-sm font-semibold text-[var(--accent-ink)] hover:brightness-105 disabled:opacity-50"
                    onClick={() => void saveAccessPassword()}
                  >
                    {settings.access_password_enabled ? t("Update password") : t("Set password")}
                  </button>
                </div>
              </section>
            </div>
          )}

          {tab === "agents" && (
            <div className="grid gap-4">
              <div className="flex flex-wrap gap-2">
                {agents.map((agent) => (
                  <button
                    key={agent.id}
                    type="button"
                    className={`inline-flex items-center gap-2 rounded-full border px-3 py-2 text-left text-sm ${
                      editingAgentId === agent.id
                        ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                        : "border-[var(--line)] bg-[var(--surface)]"
                    }`}
                    onClick={() => beginAgent(agent)}
                  >
                    <AgentAvatar
                      agent={agent}
                      iconSize={12}
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--accent)]"
                    />
                    {agent.name}
                  </button>
                ))}
                <button
                  type="button"
                  className="inline-flex items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm"
                  onClick={() => beginAgent()}
                >
                  <Plus size={15} />
                  {t("Agent")}
                </button>
              </div>

              <label className="block text-sm">
                <span className="mb-1 block text-[var(--muted)]">{t("Name")}</span>
                <input
                  className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                  value={agentDraft.name}
                  onChange={(event) => setAgentDraft({ ...agentDraft, name: event.target.value })}
                />
              </label>

              <div className="block text-sm">
                <span className="mb-2 block text-[var(--muted)]">{t("Icon")}</span>
                <div className="flex flex-col items-start gap-4 sm:flex-row">
                  <AgentAvatar
                    agent={agentDraft}
                    iconSize={22}
                    className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-[var(--accent-soft)] text-[var(--accent)]"
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap gap-1.5">
                      {AGENT_ICON_LIBRARY.map((item) => {
                        const Icon = item.icon;
                        const selected = agentDraft.icon === item.id;
                        return (
                          <button
                            key={item.id}
                            type="button"
                            title={item.label}
                            aria-label={item.label}
                            className={[
                              "flex h-9 w-9 items-center justify-center rounded-lg border transition-colors",
                              selected
                                ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--accent)]"
                                : "border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--ink)]",
                            ].join(" ")}
                            onClick={() => setAgentDraft({ ...agentDraft, icon: item.id })}
                          >
                            <Icon size={16} />
                          </button>
                        );
                      })}
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]">
                        <FileUp size={14} />
                        {iconUploading ? t("Uploading...") : t("Upload image")}
                        <input
                          type="file"
                          accept="image/*"
                          className="hidden"
                          disabled={iconUploading}
                          onChange={(event) => void uploadAgentIcon(event)}
                        />
                      </label>
                      {agentDraft.icon ? (
                        <button
                          type="button"
                          className="rounded-full px-3 py-1.5 text-xs text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                          onClick={() => setAgentDraft({ ...agentDraft, icon: "" })}
                        >
                          {t("Use auto icon")}
                        </button>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>

              <label className="block text-sm">
                <span className="mb-1 block text-[var(--muted)]">{t("Model")}</span>
                <select
                  className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                  value={agentDraft.model}
                  onChange={(event) => setAgentDraft({ ...agentDraft, model: event.target.value })}
                >
                  <option value="">{t("Use default")}</option>
                  {models.map((model) => (
                    <option value={model.name} key={model.name}>
                      {formatModel(model)}
                    </option>
                  ))}
                </select>
              </label>

              <label className="block text-sm">
                <span className="mb-1 block text-[var(--muted)]">{t("System prompt")}</span>
                <textarea
                  className="min-h-32 w-full resize-y rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                  value={agentDraft.system_prompt}
                  onChange={(event) => setAgentDraft({ ...agentDraft, system_prompt: event.target.value })}
                />
              </label>

              <div>
                <div className="mb-3 flex items-center justify-between gap-2">
                  <div>
                    <div className="text-sm font-medium text-[var(--muted)]">{t("Allowed tools")}</div>
                    <div className="font-mono text-[0.66rem] text-[var(--muted)]">{t("{n} enabled", { n: agentDraft.enabled_tools.length })}</div>
                  </div>
                  <div className="flex rounded-full bg-[var(--inset)] p-0.5">
                    {(["all", "allowed", "not_allowed"] as const).map((filter) => {
                      const active = pluginFilter === filter;
                      const label = filter === "all" ? t("All") : filter === "allowed" ? t("On") : t("Off");
                      return (
                        <button
                          key={filter}
                          type="button"
                          className={[
                            "rounded-full px-2.5 py-1 text-xs transition-colors",
                            active
                              ? "bg-[var(--surface)] text-[var(--ink)] shadow-lift"
                              : "text-[var(--muted)] hover:text-[var(--ink)]",
                          ].join(" ")}
                          onClick={() => setPluginFilter(filter)}
                        >
                          {label}
                        </button>
                      );
                    })}
                  </div>
                </div>
                <div className="grid gap-2">
                  {(() => {
                    const filteredTools = tools.filter((tool) => {
                      const isAllowed = agentDraft.enabled_tools.includes(tool.name);
                      if (pluginFilter === "allowed") return isAllowed;
                      if (pluginFilter === "not_allowed") return !isAllowed;
                      return true;
                    });
                    if (filteredTools.length === 0) {
                      return (
                        <div className="py-6 text-center text-sm text-[var(--muted)]">
                          {pluginFilter === "allowed" ? t("No tools are on") : t("No tools are off")}
                        </div>
                      );
                    }
                    return filteredTools.map((tool) => (
                      <label
                        key={tool.name}
                        className={[
                          "flex items-center justify-between rounded-lg border px-3 py-2 text-sm",
                          agentDraft.enabled_tools.includes(tool.name)
                            ? "border-[var(--accent)] bg-[var(--accent-soft)]"
                            : "border-[var(--line)] bg-[var(--surface)]",
                        ].join(" ")}
                      >
                        <span className="flex min-w-0 items-center gap-2">
                          <span
                            className="h-2 w-2 shrink-0 rounded-full"
                            style={{ backgroundColor: tool.plugin?.brand_color || (tool.connected ? "var(--green)" : "var(--muted)") }}
                          />
                          <span className="min-w-0">
                            <span className="block truncate">{tool.display_name}</span>
                            <span className="block truncate font-mono text-[0.68rem] text-[var(--muted)]">
                              {toolSourceLabel(tool)} · {t(toolRuntimeLabel(tool))} · {t(toolCapabilityLine(tool))}
                            </span>
                          </span>
                        </span>
                        <span className="flex shrink-0 items-center gap-3">
                          <span className="font-mono text-[0.68rem] text-[var(--muted)]">
                            {agentDraft.enabled_tools.includes(tool.name) ? t("On") : t("Off")}
                          </span>
                          <span className="relative inline-flex items-center">
                            <input
                              type="checkbox"
                              checked={agentDraft.enabled_tools.includes(tool.name)}
                              onChange={() => toggleAgentTool(tool.name)}
                              className="peer sr-only"
                            />
                            <div className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full peer-focus:outline-none" />
                          </span>
                        </span>
                      </label>
                    ));
                  })()}
                </div>
              </div>

              <div className="flex flex-wrap items-center gap-2 border-t border-[var(--line)] pt-4">
                <button
                  type="button"
                  className="rounded-full bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-[var(--accent-ink)] shadow-lift hover:brightness-105"
                  onClick={() => void saveAgent()}
                >
                  {editingAgentId ? t("Save agent") : t("Create agent")}
                </button>
                {editingAgentId && editingAgentId !== "default" ? (
                  <button
                    type="button"
                    className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--red)]"
                    onClick={() => deleteAgent(editingAgentId)}
                  >
                    {t("Delete")}
                  </button>
                ) : null}
              </div>
            </div>
          )}

          {tab === "memory" && (
            <MemorySettings
              settings={draftSettings}
              onUpdate={updateSetting}
              agents={agents}
              setNotice={setNotice}
            />
          )}

          {tab === "tools" && (
            <div className="space-y-3">
              {tools.map((tool) => {
                const hasInputs = tool.auth_fields.filter(
                  (field) => field.name !== "access_token" && field.name !== "refresh_token"
                ).length > 0;
                const isOpen = hasInputs && expandedTool === tool.name;
                const activeAgents = enabledAgentNames(tool, agents);
                const Element = hasInputs ? "button" : "div";
                return (
                  <div
                    key={tool.name}
                    className="rounded-lg border border-[var(--line)] bg-[var(--surface)] shadow-lift"
                  >
                    <Element
                      type={hasInputs ? "button" : undefined}
                      className={[
                        "grid w-full grid-cols-[1fr_auto] items-center gap-3 px-3 py-3 text-left",
                        hasInputs ? "" : "cursor-default select-none",
                      ].join(" ")}
                      onClick={hasInputs ? () => setExpandedTool(isOpen ? "" : tool.name) : undefined}
                    >
                      <span className="flex min-w-0 items-center gap-3">
                        <ToolGlyph tool={tool} size="compact" />
                        <span className="min-w-0 space-y-1">
                          <span className="flex min-w-0 flex-wrap items-center gap-2">
                            <span className="truncate text-sm font-medium">{tool.display_name}</span>
                            {tool.requires_auth && (
                              <span
                                className={[
                                  "shrink-0 rounded px-1.5 py-0.5 font-mono text-[0.64rem] uppercase",
                                  tool.connected ? "bg-green-600 text-white" : "bg-yellow-500 text-black",
                                ].join(" ")}
                              >
                                {t(authStatusLabel(tool))}
                              </span>
                            )}
                          </span>
                          <span className="block truncate text-xs text-[var(--muted)]">{tool.description}</span>
                        </span>
                      </span>
                      <span className="flex shrink-0 items-center gap-3 text-right">
                        <span className="grid justify-items-end gap-1">
                          <span className="font-mono text-xs text-[var(--muted)]">{t(toolCapabilityLine(tool))}</span>
                          <span className={["font-mono text-[0.66rem]", activeAgents.length ? "text-[var(--green)]" : "text-[var(--muted)]"].join(" ")}>
                            {activeAgents.length ? t("Active: {n}", { n: activeAgents.length }) : t("Not active")}
                          </span>
                        </span>

                        {hasInputs && (
                          <ChevronDown
                            size={15}
                            className={[
                              "text-[var(--muted)] transition-transform duration-150",
                              isOpen ? "rotate-180" : "",
                            ].join(" ")}
                          />
                        )}
                      </span>
                    </Element>

                    {hasInputs && isOpen && (
                      <div className="border-t border-[var(--line)] p-3">
                        <div className="mb-3 grid gap-2 text-xs text-[var(--muted)]">

                          {tool.status_message && <div className="text-[var(--red)]">{tool.status_message}</div>}
                          {tool.compatibility?.status === "bridged" &&
                          tool.compatibility.adapter_connected === false &&
                          tool.compatibility.adapter_name ? (
                            <button
                              type="button"
                              className="w-fit rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--ink)]"
                              onClick={() => setExpandedTool(tool.compatibility?.adapter_name || "")}
                            >
                              {t("Configure {name}", { name: tool.compatibility.adapter_display_name || t("adapter") })}
                            </button>
                          ) : null}
                          {tool.skills?.length ? (
                            <div className="grid gap-1">
                              {tool.skills.slice(0, 3).map((skill) => (
                                <div key={skill.path} className="rounded-lg border border-[var(--line)] bg-[var(--inset)] px-2 py-1">
                                  <span className="font-medium text-[var(--ink)]">{skill.name}</span>
                                  {skill.description ? <span> · {skill.description}</span> : null}
                                </div>
                              ))}
                            </div>
                          ) : null}
                        </div>
                        <div className="space-y-3">
                          <a
                            href={toolKeyUrl(tool)}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex w-fit items-center gap-1.5 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--accent)] hover:bg-[var(--surface-2)]"
                          >
                            {t("Find my keys")} ↗
                          </a>
                          {(() => {
                            const filteredFields = tool.auth_fields.filter(
                              (field) => field.name !== "access_token" && field.name !== "refresh_token"
                            );
                            return (
                              <>
                                {filteredFields.map((field) => {
                                  const draft = credentialDrafts[tool.name] ?? {};
                                  const value = draft[field.name] ?? "";
                                  const saved = Boolean(tool.saved_auth_fields?.[field.name]);
                                  const update = (next: string) =>
                                    setCredentialDrafts({
                                      ...credentialDrafts,
                                      [tool.name]: { ...draft, [field.name]: next },
                                    });
                                  return (
                                    <label key={field.name} className="block text-sm">
                                      <span className="mb-1 flex items-center justify-between gap-2 text-[var(--muted)]">
                                        <span>{field.label}</span>
                                        {saved ? (
                                          <span className="shrink-0 rounded-full border border-[var(--line)] bg-[var(--surface)] px-1.5 py-0.5 font-mono text-[0.62rem] uppercase text-[var(--green)]">
                                            {t("Saved")}
                                          </span>
                                        ) : null}
                                      </span>
                                      {field.type === "textarea" ? (
                                        <textarea
                                          className="min-h-24 w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                                          placeholder={field.placeholder}
                                          value={value}
                                          onChange={(event) => update(event.target.value)}
                                        />
                                      ) : field.type === "checkbox" ? (
                                        <input
                                          type="checkbox"
                                          className="h-4 w-4 rounded border-[var(--line)] text-[var(--accent)] focus:ring-[var(--accent)]"
                                          checked={["1", "true", "yes", "on"].includes(value.trim().toLowerCase())}
                                          onChange={(event) => update(event.target.checked ? "true" : "false")}
                                        />
                                      ) : field.type === "select" ? (
                                        <select
                                          className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                                          value={value}
                                          onChange={(event) => update(event.target.value)}
                                        >
                                          <option value="">{t("Select")}</option>
                                          {field.options?.map((option) => (
                                            <option key={option} value={option}>
                                              {option}
                                            </option>
                                          ))}
                                        </select>
                                      ) : (
                                        <input
                                          type={field.type === "password" ? "password" : "text"}
                                          className="w-full rounded-lg border border-[var(--line)] bg-[var(--inset)] px-3 py-2 focus:border-[var(--accent)]"
                                          placeholder={saved && field.type === "password" ? t("Saved") : field.placeholder}
                                          value={value}
                                          onChange={(event) => update(event.target.value)}
                                        />
                                      )}
                                      {field.description ? (
                                        <span className="mt-1 block text-xs leading-relaxed text-[var(--muted)]">{field.description}</span>
                                      ) : null}
                                    </label>
                                  );
                                })}
                                {filteredFields.length && !tool.requires_auth ? (
                                  <button
                                    type="button"
                                    className="rounded-full bg-[var(--accent)] px-3 py-2 text-sm font-medium text-[var(--accent-ink)]"
                                    onClick={() => saveToolCredentials(tool)}
                                  >
                                    {t("Save credentials")}
                                  </button>
                                ) : null}
                              </>
                            );
                          })()}
                          {tool.requires_auth && (
                            <button
                              type="button"
                              className="rounded-full bg-[var(--accent)] px-3 py-2 text-sm font-medium text-[var(--accent-ink)]"
                              onClick={() => connectOAuth(tool)}
                            >
                              {tool.connected ? t("Reconnect OAuth") : t("Connect OAuth")}
                            </button>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}

          {tab === "docs" && <DocsSettings tools={tools} />}
            </div>

            <div className="flex h-16 shrink-0 items-center justify-between gap-3 border-t border-[var(--line)] bg-[var(--panel)] px-5 sm:px-6">
              <span className="flex min-w-0 items-center gap-1.5 text-xs text-[var(--muted)]">
                {notice ? (
                  <>
                    <Check size={14} className="shrink-0 text-[var(--green)]" />
                    <span className="truncate">{notice}</span>
                  </>
                ) : (
                  <span className="truncate">{t("Most changes save automatically.")}</span>
                )}
              </span>
              <button
                type="button"
                className="shrink-0 rounded-full border border-[var(--line)] bg-[var(--surface)] px-5 py-2 text-sm font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]"
                onClick={onClose}
              >
                {t("Done")}
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

function BestiePanel({
  open,
  onClose,
  besties,
  activeBestieId,
  geminiConfigured,
  onSelect,
  onRefresh,
  onConnectGemini,
  setStatus,
}: {
  open: boolean;
  onClose: () => void;
  besties: Bestie[];
  activeBestieId: string;
  geminiConfigured: boolean;
  onSelect: (bestieId: string) => void;
  onRefresh: () => Promise<void>;
  onConnectGemini: () => void;
  setStatus: (status: string) => void;
}) {
  const { t } = useT();
  const [editing, setEditing] = useState<null | { id: string }>(null); // null = list view; {id:""} = create
  const [name, setName] = useState("");
  const [personality, setPersonality] = useState("");
  const [avatarFileId, setAvatarFileId] = useState(""); // committed (stylized) avatar
  const [photoFile, setPhotoFile] = useState<File | null>(null);
  const [photoPreview, setPhotoPreview] = useState("");
  const [stylizing, setStylizing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState("");

  const avatarUrl = (fileId: string) => `${API_BASE}/api/files/${fileId}`;

  const beginCreate = () => {
    setEditing({ id: "" });
    setName("");
    setPersonality("");
    setAvatarFileId("");
    setPhotoFile(null);
    setPhotoPreview("");
    setNote("");
  };

  const beginEdit = (bestie: Bestie) => {
    setEditing({ id: bestie.id });
    setName(bestie.name);
    setPersonality(bestie.personality);
    setAvatarFileId(bestie.avatar);
    setPhotoFile(null);
    setPhotoPreview(bestie.avatar ? avatarUrl(bestie.avatar) : "");
    setNote("");
  };

  const closeEditor = () => setEditing(null);

  const pickPhoto = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      setNote(t("Choose an image file"));
      return;
    }
    setPhotoFile(file);
    setAvatarFileId(""); // needs (re)stylizing before it can be saved
    setPhotoPreview(URL.createObjectURL(file));
    setNote("");
  };

  // Send the photo through Gemini (or the raw-photo fallback) and keep the resulting file id.
  const stylize = async (): Promise<string> => {
    if (!photoFile) return avatarFileId;
    setStylizing(true);
    try {
      const body = new FormData();
      body.append("file", photoFile);
      const response = await fetch(`${API_BASE}/api/bestie/stylize`, { method: "POST", credentials: "include", body });
      if (!response.ok) {
        setNote(await response.text());
        return "";
      }
      const result = (await response.json()) as { file_id: string; stylized: boolean };
      setAvatarFileId(result.file_id);
      setPhotoFile(null);
      setPhotoPreview(avatarUrl(result.file_id));
      setNote(result.stylized ? t("Stylized!") : t("Saved your photo. Connect Gemini in Settings to restyle it."));
      return result.file_id;
    } catch (error) {
      setNote((error as Error).message);
      return "";
    } finally {
      setStylizing(false);
    }
  };

  const save = async () => {
    if (!name.trim() || !editing) return;
    setSaving(true);
    try {
      // Make sure the chosen photo has been turned into an avatar file first.
      let avatar = avatarFileId;
      if (!avatar && photoFile) avatar = await stylize();
      const payload = { name: name.trim(), personality: personality.trim(), avatar };
      if (editing.id) {
        await api(`/api/besties/${editing.id}`, { method: "PUT", body: JSON.stringify(payload) });
      } else {
        await api("/api/besties", { method: "POST", body: JSON.stringify(payload) });
      }
      await onRefresh();
      setStatus(t("Bestie saved"));
      closeEditor();
    } catch (error) {
      setNote((error as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (bestie: Bestie) => {
    if (typeof window !== "undefined" && !window.confirm(t("Delete {name}?", { name: bestie.name }))) return;
    try {
      await api(`/api/besties/${bestie.id}`, { method: "DELETE" });
      await onRefresh();
      setStatus(t("Bestie deleted"));
    } catch (error) {
      setStatus((error as Error).message);
    }
  };

  const cards: Array<{ id: string; name: string; personality: string; avatar: string; builtIn: boolean }> = [
    { id: "", name: "Sammy", personality: t("Your original local companion."), avatar: SAMMY_LOGO, builtIn: true },
    ...besties.map((bestie) => ({
      id: bestie.id,
      name: bestie.name,
      personality: bestie.personality,
      avatar: bestie.avatar ? avatarUrl(bestie.avatar) : SAMMY_LOGO,
      builtIn: false,
    })),
  ];
  const showGeminiSetup = !geminiConfigured;

  return (
    <>
      {open ? (
        <button
          type="button"
          className="bestie-scrim fixed inset-0 z-30 cursor-default bg-[color:rgba(44,33,27,0.3)] backdrop-blur-[2px]"
          aria-label={t("Close")}
          onClick={onClose}
        />
      ) : null}
      <div
        className={[
          "bestie-panel fixed inset-y-0 right-0 z-40 flex w-full max-w-[560px] flex-col border-l border-[var(--line)] bg-[var(--panel)] shadow-popover",
          open ? "translate-x-0" : "translate-x-full",
        ].join(" ")}
      >
        <div className="flex h-16 shrink-0 items-center justify-between border-b border-[var(--line)] px-5">
          <div className="flex items-center gap-2.5">
            <div className="leading-tight">
              <div className="font-display text-lg font-semibold tracking-tight">{t("My Bestie")}</div>
              <div className="text-xs text-[var(--muted)]">{t("Choose who you're chatting with, or create your own.")}</div>
            </div>
          </div>
          <button
            type="button"
            className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
            onClick={onClose}
            title={t("Close")}
            aria-label={t("Close")}
          >
            <X size={18} />
          </button>
        </div>

        <div className="scrollbar flex-1 overflow-y-auto p-5 sm:p-6">
          <div className="mx-auto w-full max-w-2xl">

        {showGeminiSetup ? (
          <button
            type="button"
            onClick={onConnectGemini}
            className="mt-5 flex w-full items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--surface)] px-4 py-3 text-left text-sm hover:bg-[var(--surface-2)]"
          >
            <Sparkles size={16} className="shrink-0 text-[var(--accent)]" />
            <span className="min-w-0">
              <span className="block font-medium text-[var(--ink)]">{t("Connect Gemini to restyle your photos")}</span>
              <span className="block text-xs text-[var(--muted)]">{t("Without it, your photo is used as-is. Tap to add a key in Settings.")}</span>
            </span>
          </button>
        ) : null}

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cards.map((card) => {
            const active = card.id === activeBestieId;
            return (
              <div
                key={card.id || "sammy"}
                className={[
                  "flex items-start gap-3 rounded-2xl border p-4 shadow-lift",
                  active ? "border-[var(--accent)] bg-[var(--accent-soft)]" : "border-[var(--line)] bg-[var(--surface)]",
                ].join(" ")}
              >
                <img
                  src={card.avatar}
                  alt={card.name}
                  className="h-14 w-14 shrink-0 rounded-full bg-white object-contain"
                  draggable={false}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate font-display text-base font-semibold">{card.name}</span>
                    {active ? (
                      <span className="shrink-0 rounded-full bg-[var(--accent)] px-2 py-0.5 text-[0.62rem] font-semibold text-[var(--accent-ink)]">
                        {t("Active")}
                      </span>
                    ) : null}
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-xs text-[var(--muted)]">{card.personality || t("No personality set.")}</p>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {active ? (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-[var(--accent)]">
                        <Check size={14} /> {t("Chatting now")}
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => onSelect(card.id)}
                        className="rounded-full bg-[var(--accent)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-ink)] hover:brightness-105"
                      >
                        {t("Use this bestie")}
                      </button>
                    )}
                    {!card.builtIn ? (
                      <>
                        <button
                          type="button"
                          onClick={() => beginEdit(besties.find((b) => b.id === card.id)!)}
                          className="rounded-full p-1.5 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                          title={t("Edit")}
                          aria-label={t("Edit")}
                        >
                          <PenLine size={15} />
                        </button>
                        <button
                          type="button"
                          onClick={() => void remove(besties.find((b) => b.id === card.id)!)}
                          className="rounded-full p-1.5 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--red)]"
                          title={t("Delete")}
                          aria-label={t("Delete")}
                        >
                          <Trash2 size={15} />
                        </button>
                      </>
                    ) : null}
                  </div>
                </div>
              </div>
            );
          })}

          {!editing ? (
            <button
              type="button"
              onClick={beginCreate}
              className="flex min-h-[7rem] items-center justify-center gap-2 rounded-2xl border border-dashed border-[var(--line-strong)] bg-[var(--inset)] p-4 text-sm font-medium text-[var(--muted)] hover:border-[var(--accent)] hover:text-[var(--ink)]"
            >
              <Plus size={18} />
              {t("Create a bestie")}
            </button>
          ) : null}
        </div>

        {editing ? (
          <section className="mt-5 rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-5 shadow-lift">
            <div className="flex items-center justify-between">
              <h2 className="font-display text-lg font-semibold tracking-tight">
                {editing.id ? t("Edit bestie") : t("Create a bestie")}
              </h2>
              <button
                type="button"
                onClick={closeEditor}
                className="rounded-full p-2 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                title={t("Close")}
                aria-label={t("Close")}
              >
                <X size={18} />
              </button>
            </div>

            <div className="mt-4 flex flex-col gap-4 sm:flex-row sm:items-start">
              <div className="flex shrink-0 flex-col items-center gap-2">
                <span className="flex h-24 w-24 items-center justify-center overflow-hidden rounded-full border border-[var(--line)] bg-[var(--inset)]">
                  {photoPreview ? (
                    <img src={photoPreview} alt={t("Bestie avatar")} className="h-24 w-24 object-contain" draggable={false} />
                  ) : (
                    <Sparkles size={24} className="text-[var(--muted)]" />
                  )}
                </span>
                <label className="inline-flex cursor-pointer items-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] px-3 py-1.5 text-xs font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]">
                  <FileUp size={14} />
                  {t("Upload photo")}
                  <input type="file" accept="image/*" className="hidden" onChange={pickPhoto} />
                </label>
                {photoFile ? (
                  <button
                    type="button"
                    onClick={() => void stylize()}
                    disabled={stylizing}
                    className="inline-flex items-center gap-2 rounded-full bg-[var(--accent)] px-3 py-1.5 text-xs font-semibold text-[var(--accent-ink)] disabled:opacity-50"
                  >
                    <Sparkles size={14} />
                    {stylizing ? t("Stylizing…") : t("Stylize with Gemini")}
                  </button>
                ) : null}
              </div>

              <div className="min-w-0 flex-1 space-y-3">
                <label className="block text-sm">
                  <span className="mb-1 block font-medium text-[var(--ink)]">{t("Name")}</span>
                  <input
                    className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                    placeholder={t("e.g. Mochi")}
                  />
                </label>
                <label className="block text-sm">
                  <span className="mb-1 block font-medium text-[var(--ink)]">{t("Personality")}</span>
                  <textarea
                    className="min-h-24 w-full resize-y rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
                    value={personality}
                    onChange={(event) => setPersonality(event.target.value)}
                    placeholder={t("Describe how this bestie talks and behaves.")}
                  />
                </label>
                {note ? <p className="text-xs text-[var(--muted)]">{note}</p> : null}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void save()}
                    disabled={saving || stylizing || !name.trim()}
                    className="rounded-full bg-[var(--accent)] px-5 py-2 text-sm font-semibold text-[var(--accent-ink)] shadow-lift hover:brightness-105 disabled:opacity-50"
                  >
                    {saving ? t("Saving…") : editing.id ? t("Save bestie") : t("Create bestie")}
                  </button>
                  <button
                    type="button"
                    onClick={closeEditor}
                    className="rounded-full border border-[var(--line)] bg-[var(--surface)] px-4 py-2 text-sm font-medium text-[var(--ink)] hover:bg-[var(--surface-2)]"
                  >
                    {t("Cancel")}
                  </button>
                </div>
              </div>
            </div>
          </section>
        ) : null}
          </div>
        </div>
      </div>
    </>
  );
}

// Mandatory on first run / whenever no password is set. Non-dismissible — closes only once a
// password is saved (which flips authStatus.password_required to true).
function PasswordSetupModal({ onSubmit }: { onSubmit: (password: string) => Promise<void> }) {
  const { t } = useT();
  const [pw, setPw] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const submit = async () => {
    if (pw.length < 4) {
      setError(t("Use at least 4 characters."));
      return;
    }
    if (pw !== confirm) {
      setError(t("Passwords don't match."));
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      await onSubmit(pw);
    } catch (e) {
      setError((e as Error).message);
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-[color:rgba(44,33,27,0.55)] p-4 backdrop-blur-sm">
      <div className="w-full max-w-sm rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-6 shadow-popover">
        <div className="flex items-center gap-2.5">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-2xl bg-[var(--accent-soft)] text-[var(--accent)]">
            <ShieldCheck size={20} />
          </span>
          <div className="leading-tight">
            <h2 className="font-display text-lg font-semibold tracking-tight">{t("Set a password")}</h2>
            <p className="mt-0.5 text-xs text-[var(--muted)]">
              {t("Protects Sammy — required before anyone (or your phone) can open it.")}
            </p>
          </div>
        </div>
        <form
          className="mt-4 space-y-3"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <input
            type="password"
            autoFocus
            autoComplete="new-password"
            className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
            placeholder={t("New password")}
            value={pw}
            onChange={(event) => setPw(event.target.value)}
          />
          <input
            type="password"
            autoComplete="new-password"
            className="w-full rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2.5 focus:border-[var(--accent)]"
            placeholder={t("Confirm password")}
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
          {error ? <p className="text-xs text-[var(--red)]">{error}</p> : null}
          <button
            type="submit"
            disabled={submitting || !pw || !confirm}
            className="w-full rounded-full bg-[var(--accent)] px-5 py-2.5 text-sm font-semibold text-[var(--accent-ink)] shadow-lift hover:brightness-105 disabled:opacity-50"
          >
            {submitting ? t("Setting…") : t("Set password")}
          </button>
        </form>
      </div>
    </div>
  );
}

export default function App() {
  const { t, lang } = useT();
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedConversationId, setSelectedConversationId] = useState("");
  const [selectedConversationMode, setSelectedConversationMode] = useState<"chat" | "tool_builder">("chat");
  const [messages, setMessages] = useState<Message[]>([]);
  const [settings, setSettings] = useState<SettingsShape>({
    default_model: "",
    system_prompt: "",
    num_ctx: 8192,
    num_predict: 2048,
    temperature: 0.2,
    think: false,
    theme: "light",
    access_password_enabled: false,
    memory_mode: "auto",
    memory_recall_enabled: true,
    memory_recall_limit: 5,
    elevenlabs_enabled: false,
    elevenlabs_configured: false,
    elevenlabs_voice_id: "",
    voice_auth_enabled: false,
    picovoice_access_key: "",
    picovoice_speaker_profile: "",
    active_bestie_id: "",
    gemini_configured: false,
  });
  const [agents, setAgents] = useState<Agent[]>([]);
  const [besties, setBesties] = useState<Bestie[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState("default");
  const [selectedModel, setSelectedModel] = useState("");
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginSubmitting, setLoginSubmitting] = useState(false);
  const [input, setInput] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [bestieOpen, setBestieOpen] = useState(false);
  const [networkOpen, setNetworkOpen] = useState(false);
  const [networkInfo, setNetworkInfo] = useState<NetworkInfo | null>(null);
  const [networkCopied, setNetworkCopied] = useState(""); // the URL most recently copied
  const [settingsInitialTab, setSettingsInitialTab] = useState<SettingsTab>("general");
  const [settingsFocusTarget, setSettingsFocusTarget] = useState<SettingsFocusTarget>(null);
  const [activePage, setActivePage] = useState<"chat" | "tools">("chat");
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [activeUserActionId, setActiveUserActionId] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [generationState, setGenerationState] = useState<GenerationState | null>(null);
  const [activeJobConversationId, setActiveJobConversationId] = useState("");
  const [status, setStatus] = useState("");
  const [attachments, setAttachments] = useState<string[]>([]);
  const [pluginMenuOpen, setPluginMenuOpen] = useState(false);
  const [pluginMenuView, setPluginMenuView] = useState<"active" | "add">("active");
  const [reasoningMenuOpen, setReasoningMenuOpen] = useState(false);
  const [agentPickerOpen, setAgentPickerOpen] = useState(false);
  const [agentPickerMode, setAgentPickerMode] = useState<"new" | "send">("new");
  const [collapsedAgentGroups, setCollapsedAgentGroups] = useState<Record<string, boolean>>({});
  const [listening, setListening] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const activeJobIdRef = useRef("");
  const lastJobEventIdRef = useRef(0);
  const streamingMessageIdRef = useRef("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const pluginMenuButtonRef = useRef<HTMLButtonElement | null>(null);
  const pluginMenuRef = useRef<HTMLDivElement | null>(null);
  const reasoningMenuButtonRef = useRef<HTMLButtonElement | null>(null);
  const reasoningMenuRef = useRef<HTMLDivElement | null>(null);
  const recognitionRef = useRef<any>(null);
  const voiceBaseInputRef = useRef("");
  const voiceFinalTranscriptRef = useRef("");
  const [voiceMode, setVoiceMode] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const wakeRecognitionRef = useRef<any>(null);
  const startWakeRef = useRef<() => void>(() => {});
  const voiceModeRef = useRef(false);
  const speakingRef = useRef(false);
  const pendingSpeakRef = useRef(false);
  const pendingSpeakFullRef = useRef(false);
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);
  const keepSpeakingOnNextSendRef = useRef(false);
  const engagedRef = useRef(false);
  const engageTimerRef = useRef<number | null>(null);
  const voiceAuthActiveRef = useRef(false);
  const ownerSpokeAtRef = useRef(0);
  const generatingRef = useRef(false);
  const messagesRef = useRef<Message[]>([]);
  const identityNameRef = useRef("Sammy");
  const langRef = useRef(lang);
  const wakeGreetingLastSpokenAtRef = useRef(0);
  const handleVoiceCommandRef = useRef<(command: string, acknowledgement?: string) => void>(() => {});
  const prevGeneratingRef = useRef(false);

  const selectedAgent = useMemo(
    () => agents.find((agent) => agent.id === selectedAgentId) ?? agents[0],
    [agents, selectedAgentId]
  );
  const activeToolNames = selectedAgent?.enabled_tools ?? [];
  const activeTools = tools.filter((tool) => activeToolNames.includes(tool.name));
  const inactiveTools = tools.filter((tool) => !activeToolNames.includes(tool.name));
  const disconnectedActiveTools = activeTools.filter(needsPluginReconnect);
  const disconnectedPluginNames = disconnectedActiveTools.map((tool) => tool.display_name).join(", ");
  const disconnectedPluginStatus = disconnectedActiveTools.length
    ? `${disconnectedPluginNames} ${disconnectedActiveTools.length === 1 ? "needs" : "need"} reconnecting.`
    : "";
  const activePluginCountLabel = activeTools.length > 20 ? "20+" : String(activeTools.length);
  const activePluginShortLabel = activeTools.length === 1 ? t("1 tool") : t("{n} tools", { n: activeTools.length });
  const activePluginFullLabel = disconnectedActiveTools.length
    ? disconnectedPluginStatus
    : activeTools.length === 1
      ? t("1 tool active")
      : t("{n} tools active", { n: activeTools.length });
  const composerStatus = status || disconnectedPluginStatus;
  const composerStatusIsReconnect = !status && Boolean(disconnectedPluginStatus);

  useEffect(() => {
    void checkAuthStatus();
  }, []);

  useEffect(() => {
    const requireLogin = () => setAuthStatus({ password_required: true, authenticated: false });
    window.addEventListener("sammy-auth-required", requireLogin);
    return () => window.removeEventListener("sammy-auth-required", requireLogin);
  }, []);

  useEffect(() => {
    if (!authStatus || (authStatus.password_required && !authStatus.authenticated)) return;
    void loadInitial();
  }, [authStatus?.password_required, authStatus?.authenticated]);

  useEffect(() => {
    document.documentElement.dataset.theme = settings.theme || "light";
  }, [settings.theme]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, generating]);

  useEffect(() => {
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    setVoiceSupported(Boolean(SpeechRecognition));
  }, []);

  useEffect(() => {
    const closeMenus = (event: MouseEvent) => {
      const target = event.target as Node;
      const clickedPluginMenu = pluginMenuRef.current?.contains(target) || pluginMenuButtonRef.current?.contains(target);
      const clickedReasoningMenu = reasoningMenuRef.current?.contains(target) || reasoningMenuButtonRef.current?.contains(target);

      if (!clickedPluginMenu) {
        setPluginMenuOpen(false);
        setPluginMenuView("active");
      }
      if (!clickedReasoningMenu) {
        setReasoningMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", closeMenus);
    return () => document.removeEventListener("mousedown", closeMenus);
  }, []);

  async function checkAuthStatus() {
    try {
      const response = await fetch(`${API_BASE}/api/auth/status`, { credentials: "include" });
      if (!response.ok) throw new Error(await response.text());
      setAuthStatus((await response.json()) as AuthStatus);
      setLoginError("");
    } catch (error) {
      setAuthStatus({ password_required: false, authenticated: true });
      setStatus(t('Backend unavailable. Run "sammy restart" in Terminal. {detail}', { detail: (error as Error).message }));
    }
  }

  // First-run / no-password gate: save the password (the backend also starts a session),
  // then flip auth state so the mandatory modal closes.
  async function setInitialPassword(password: string) {
    const next = await api<SettingsShape>("/api/settings", {
      method: "PUT",
      body: JSON.stringify({ access_password: password }),
    });
    setSettings(next);
    setAuthStatus({ password_required: true, authenticated: true });
  }

  async function submitLogin() {
    if (!loginPassword || loginSubmitting) return;
    setLoginSubmitting(true);
    setLoginError("");
    try {
      const response = await fetch(`${API_BASE}/api/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: loginPassword }),
      });
      if (!response.ok) throw new Error(response.status === 401 ? t("Incorrect password") : await response.text());
      setAuthStatus((await response.json()) as AuthStatus);
      setLoginPassword("");
    } catch (error) {
      setLoginError((error as Error).message);
    } finally {
      setLoginSubmitting(false);
    }
  }

  async function loadInitial() {
    try {
      const [modelData, settingsData, agentData, bestieData, pluginData, conversationData] = await Promise.all([
        api<{ models: ModelInfo[] }>("/api/models"),
        api<SettingsShape>("/api/settings"),
        api<{ agents: Agent[] }>("/api/agents"),
        api<{ besties: Bestie[] }>("/api/besties"),
        api<{ plugins: ToolInfo[] }>("/api/plugins"),
      api<{ conversations: Conversation[] }>("/api/conversations"),
      ]);
      setModels(modelData.models);
      const nextModel = resolveModelName(settingsData.default_model, modelData.models);
      const nextSettings = settingsData.default_model !== nextModel ? { ...settingsData, default_model: nextModel } : settingsData;
      setSettings(nextSettings);
      setAgents(agentData.agents);
      setBesties(bestieData.besties);
      setTools(pluginData.plugins);
      setConversations(conversationData.conversations);
      setSelectedModel(nextModel);
      setSelectedAgentId(agentData.agents[0]?.id ?? "default");
      setStatus("");
      void resumeActiveChatJob(modelData.models);
      if (settingsData.default_model !== nextModel) {
        void api<SettingsShape>("/api/settings", {
          method: "PUT",
          body: JSON.stringify(nextSettings),
        })
          .then(setSettings)
          .catch(() => undefined);
      }
    } catch (error) {
      setStatus(t('Backend unavailable. Run "sammy restart" in Terminal. {detail}', { detail: (error as Error).message }));
    }
  }

  async function refreshConversations() {
    const data = await api<{ conversations: Conversation[] }>("/api/conversations");
    setConversations(data.conversations);
  }

  async function refreshTools() {
    const data = await api<{ plugins: ToolInfo[] }>("/api/plugins");
    setTools(data.plugins);
  }

  async function refreshAgents() {
    const data = await api<{ agents: Agent[] }>("/api/agents");
    setAgents(data.agents);
  }

  function openSettings(tab: SettingsTab = "general", focusTarget: SettingsFocusTarget = null) {
    setSettingsInitialTab(tab);
    setSettingsFocusTarget(focusTarget);
    setSettingsOpen(true);
  }

  function openToolsPage() {
    setActivePage("tools");
    setMobileNavOpen(false);
    setPluginMenuOpen(false);
    setReasoningMenuOpen(false);
  }

  function openBestiePage() {
    setBestieOpen(true);
    setMobileNavOpen(false);
    setPluginMenuOpen(false);
    setReasoningMenuOpen(false);
  }

  async function openNetworkPopup() {
    setNetworkOpen(true);
    setNetworkCopied("");
    try {
      setNetworkInfo(await api<NetworkInfo>("/api/network"));
    } catch {
      setNetworkInfo(null);
    }
  }

  async function refreshBesties() {
    const data = await api<{ besties: Bestie[] }>("/api/besties");
    setBesties(data.besties);
  }

  async function updateActiveBestie(bestieId: string) {
    const previous = settings;
    setSettings({ ...settings, active_bestie_id: bestieId }); // optimistic — identity swaps instantly
    try {
      const next = await api<SettingsShape>("/api/settings", {
        method: "PUT",
        body: JSON.stringify({ active_bestie_id: bestieId }),
      });
      setSettings(next);
    } catch (error) {
      setSettings(previous);
      setStatus((error as Error).message);
    }
  }

  async function openConversation(conversationId: string) {
    if (generating && activeJobConversationId) {
      if (conversationId !== activeJobConversationId) {
        setStatus(t("Sammy is still working. Stop the current task before switching conversations."));
      }
      return;
    }
    const data = await api<{ conversation: Conversation; messages: Message[] }>(`/api/conversations/${conversationId}`);
    setActivePage("chat");
    setAgentPickerOpen(false);
    setSelectedConversationId(conversationId);
    setSelectedConversationMode(data.conversation.mode === "tool_builder" ? "tool_builder" : "chat");
    setMessages(data.messages);
    setSelectedModel(resolveModelName(data.conversation.model || selectedModel, models));
    setSelectedAgentId(data.conversation.agent_id || "default");
    setAgentGroupCollapsed(data.conversation.agent_id || "default", false);
  }

  function openAgentPicker(mode: "new" | "send" = "new") {
    if (generating) {
      setStatus(t("Sammy is still working. Stop the current task before starting another chat."));
      return;
    }
    setActivePage("chat");
    setAgentPickerMode(mode);
    setAgentPickerOpen(true);
    setMobileNavOpen(false);
    setPluginMenuOpen(false);
    setReasoningMenuOpen(false);
  }

  function setAgentGroupCollapsed(agentId: string, collapsed: boolean) {
    setCollapsedAgentGroups((current) => {
      if (current[agentId] === collapsed) return current;
      return { ...current, [agentId]: collapsed };
    });
  }

  function toggleAgentGroup(agentId: string) {
    setCollapsedAgentGroups((current) => ({ ...current, [agentId]: !current[agentId] }));
  }

  async function newChat(agentId?: string) {
    const targetAgentId = agentId || selectedAgentId || agents[0]?.id || "default";
    setActivePage("chat");
    const created = await api<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        title: "New chat",
        model: selectedModel,
        agent_id: targetAgentId,
        mode: "chat",
      }),
    });
    setAgentPickerOpen(false);
    setSelectedConversationId(created.id);
    setSelectedConversationMode("chat");
    setSelectedAgentId(created.agent_id || targetAgentId);
    setAgentGroupCollapsed(created.agent_id || targetAgentId, false);
    setSelectedModel(resolveModelName(created.model || selectedModel, models));
    setMessages([]);
    await Promise.allSettled([refreshConversations(), refreshTools()]);
  }

  async function startToolBuilder() {
    if (generating) {
      setStatus(t("Sammy is still working. Stop the current task before starting another chat."));
      return;
    }
    const targetAgentId = selectedAgentId || agents[0]?.id || "default";
    const created = await api<Conversation>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        title: "Build a tool",
        model: selectedModel,
        agent_id: targetAgentId,
        mode: "tool_builder",
      }),
    });
    setActivePage("chat");
    setMobileNavOpen(false);
    setAgentPickerOpen(false);
    setSelectedConversationId(created.id);
    setSelectedConversationMode("tool_builder");
    setSelectedAgentId(created.agent_id || targetAgentId);
    setSelectedModel(resolveModelName(created.model || selectedModel, models));
    setAgentGroupCollapsed(created.agent_id || targetAgentId, false);
    setMessages([]);
    setInput("");
    setStatus("");
    await refreshConversations();
  }

  function chooseAgentForStart(agentId: string) {
    setAgentPickerOpen(false);
    if (agentPickerMode === "send") {
      void sendMessage({ content: input, agent_id: agentId });
      return;
    }
    void newChat(agentId);
  }

  async function openMobileConversation(conversationId: string) {
    setMobileNavOpen(false);
    await openConversation(conversationId);
  }

  function createMobileChat() {
    openAgentPicker("new");
  }

  async function togglePin(conversation: Conversation) {
    await api<Conversation>(`/api/conversations/${conversation.id}`, {
      method: "PATCH",
      body: JSON.stringify({ pinned: conversation.pinned ? 0 : 1 }),
    });
    await refreshConversations();
  }

  async function deleteConversation(conversationId: string) {
    if (generating && conversationId === activeJobConversationId) {
      setStatus(t("Stop the active task before deleting this conversation."));
      return;
    }
    await api(`/api/conversations/${conversationId}`, { method: "DELETE" });
    if (selectedConversationId === conversationId) {
      setSelectedConversationId("");
      setSelectedConversationMode("chat");
      setMessages([]);
    }
    await refreshConversations();
  }

  function selectedOptions() {
    return {
      num_ctx: settings.num_ctx,
      num_predict: settings.num_predict,
      temperature: settings.temperature,
      think: settings.think,
    };
  }

  function markStreamingResponse(streamId: string, metadata: Record<string, any>) {
    if (!streamId) return;
    setMessages((items) =>
      updateStreamingMessage(items, streamId, (message) => ({
        ...message,
        metadata: {
          ...message.metadata,
          ...metadata,
        },
      }))
    );
  }

  async function updateSelectedAgentTools(nextEnabledTools: string[]) {
    if (!selectedAgent) return;
    const nextAgent = { ...selectedAgent, enabled_tools: nextEnabledTools };
    setAgents((items) => items.map((agent) => (agent.id === selectedAgent.id ? nextAgent : agent)));
    try {
      await api<Agent>(`/api/agents/${selectedAgent.id}`, {
        method: "PUT",
        body: JSON.stringify({
          name: nextAgent.name,
          system_prompt: nextAgent.system_prompt,
          model: nextAgent.model,
          icon: nextAgent.icon || "",
          enabled_tools: nextAgent.enabled_tools,
        }),
      });
      setStatus("");
    } catch (error) {
      setStatus(`Could not update tools. ${(error as Error).message}`);
    }
  }

  function setSelectedAgentPlugin(toolName: string, enabled: boolean) {
    const next = new Set(activeToolNames);
    if (enabled) next.add(toolName);
    else next.delete(toolName);
    void updateSelectedAgentTools(Array.from(next));
  }

  function connectPluginOAuth(tool: ToolInfo) {
    if (!hasSavedOAuthClient(tool)) {
      setPluginMenuOpen(false);
      openSettings("tools");
      setStatus(`${tool.display_name} needs a saved Client ID and Client Secret first.`);
      return;
    }

    const popup = window.open(`${API_BASE}/api/plugins/${tool.name}/oauth/start`, "_blank");
    if (!popup) {
      setStatus(`Popup blocked. Allow popups to reconnect ${tool.display_name}.`);
      return;
    }
    setStatus(`Reconnecting ${tool.display_name}...`);
    const timer = window.setInterval(() => {
      if (!popup.closed) return;
      window.clearInterval(timer);
      void refreshTools().then(() => setStatus(`${tool.display_name} OAuth status updated.`));
    }, 800);
  }

  async function setReasoningMode(think: boolean) {
    const next = { ...settings, think };
    setSettings(next);
    setReasoningMenuOpen(false);
    try {
      const saved = await api<SettingsShape>("/api/settings", {
        method: "PUT",
        body: JSON.stringify(next),
      });
      setSettings(saved);
      setStatus("");
    } catch (error) {
      setStatus(`Could not update reasoning mode. ${(error as Error).message}`);
    }
  }

  async function toggleVoiceInput() {
    if (listening) {
      recognitionRef.current?.stop?.();
      setListening(false);
      setStatus("");
      return;
    }

    if (voiceMode) setVoiceMode(false);

    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setStatus(t("Voice input is not supported in this browser."));
      return;
    }
    if (!window.isSecureContext) {
      setStatus(t("Voice input needs localhost or HTTPS."));
      return;
    }

    if (navigator.mediaDevices?.getUserMedia) {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        stream.getTracks().forEach((track) => track.stop());
      } catch (error) {
        setStatus(`Microphone access was blocked. ${(error as Error).message}`);
        return;
      }
    }

    const recognition = new SpeechRecognition();
    let hadError = false;
    let heardAny = false;
    voiceBaseInputRef.current = input.trimEnd();
    voiceFinalTranscriptRef.current = "";
    recognition.lang = navigator.language || "en-US";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;
    recognition.onresult = (event: any) => {
      let finalText = "";
      let interimText = "";
      for (let index = 0; index < event.results.length; index += 1) {
        const spoken = String(event.results[index][0]?.transcript || "").trim();
        if (!spoken) continue;
        heardAny = true;
        if (event.results[index].isFinal) {
          finalText = [finalText, spoken].filter(Boolean).join(" ");
        } else {
          interimText = [interimText, spoken].filter(Boolean).join(" ");
        }
      }
      voiceFinalTranscriptRef.current = finalText.trim();
      const transcript = [voiceFinalTranscriptRef.current, interimText.trim()].filter(Boolean).join(" ");
      const base = voiceBaseInputRef.current;
      setInput([base, transcript].filter(Boolean).join(" "));
      setStatus(interimText ? t("Listening...") : t("Heard it. Keep speaking or tap the mic to stop."));
    };
    recognition.onerror = (event: any) => {
      hadError = true;
      const errorMessages: Record<string, string> = {
        "not-allowed": "Chrome blocked microphone access for Sammy.",
        "service-not-allowed": "Chrome blocked the speech recognition service.",
        "no-speech": "Sammy did not hear speech. Try again closer to the mic.",
        "audio-capture": "Chrome could not find a working microphone.",
        network: "Chrome could not reach the speech recognition service.",
      };
      setStatus(errorMessages[event.error] || `Voice input error: ${event.error || "unknown error"}`);
      setListening(false);
    };
    recognition.onend = () => {
      recognitionRef.current = null;
      setListening(false);
      if (!heardAny && !hadError) {
        setStatus(t("Sammy did not catch anything. Tap the mic and try again."));
      } else if (!hadError) {
        setStatus("");
      }
    };
    recognitionRef.current = recognition;
    setListening(true);
    setStatus(t("Listening..."));
    try {
      recognition.start();
    } catch (error) {
      setListening(false);
      setStatus(`Voice input could not start. ${(error as Error).message}`);
    }
  }

  function wordCount(text: string) {
    return (text.match(/\S+/g) ?? []).length;
  }

  function truncateWords(text: string, maxWords: number) {
    const words = text.match(/\S+/g) ?? [];
    if (words.length <= maxWords) return text;
    return `${words.slice(0, maxWords).join(" ").replace(/[,:;.-]+$/, "")}.`;
  }

  function wantsFullVoiceReadback(command: string) {
    const lower = command.toLowerCase();
    return (
      /\b(?:read|say|speak|tell me|go through|walk me through)\b[^.?!]{0,80}\b(?:full|whole|entire|all|everything|verbatim|detailed?|details?)\b/.test(lower) ||
      /\b(?:read|say|speak)\b[^.?!]{0,40}\b(?:it all|all of it|the whole thing|the full thing)\b/.test(lower) ||
      /\b(?:don't|do not)\s+summari[sz]e\b/.test(lower)
    );
  }

  function cleanForSpeech(text: string, maxChars = 1200) {
    const clean = text
      .replace(/```[\s\S]*?```/g, " (code snippet) ")
      .replace(/`([^`]+)`/g, "$1")
      .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/https?:\/\/\S+/g, "")
      .replace(/^#{1,6}\s+/gm, "")
      .replace(/^\s*[-*+]\s+/gm, "")
      .replace(/\n{2,}/g, ". ")
      .replace(/\n/g, ". ")
      .replace(/[*_>#~|]/g, "")
      // Never read emoji aloud: strip pictographs, flags, skin tones, ZWJ, variation selectors, keycaps.
      .replace(/\p{Extended_Pictographic}/gu, "")
      .replace(/[\u{1F1E6}-\u{1F1FF}\u{1F3FB}-\u{1F3FF}\u200D\uFE0F\u20E3]/gu, "")
      .replace(/\s+([,.!?;:])/g, "$1")
      .replace(/\.{2,}/g, ".")
      .replace(/\s+/g, " ")
      .trim();
    if (!maxChars || clean.length <= maxChars) return clean;
    return `${clean.slice(0, maxChars).replace(/\s+\S*$/, "").replace(/[,:;.-]+$/, "")}.`;
  }

  function sentenceChunks(text: string) {
    return (text.match(/[^.!?]+[.!?]+|[^.!?]+$/g) ?? [])
      .map((part) => part.trim())
      .filter(Boolean);
  }

  function spokenSummaryFor(text: string) {
    const clean = cleanForSpeech(text, FULL_SPEECH_MAX_CHARS);
    if (!clean) return "";
    if (wordCount(clean) <= SPOKEN_SHORT_REPLY_MAX_WORDS && clean.length <= SPOKEN_SHORT_REPLY_MAX_CHARS) return clean;

    const picked: string[] = [];
    let totalWords = 0;
    for (const raw of sentenceChunks(clean)) {
      let sentence = raw
        .replace(/^(?:sure|okay|ok|got it|absolutely|of course)[,.!\s-]+/i, "")
        .replace(/^here(?:'s| is)(?: what i found| the summary| the breakdown| the answer)?[:,\s-]*/i, "")
        .trim();
      if (!sentence || /^(?:read|see) the chat\b/i.test(sentence)) continue;
      const nextWords = wordCount(sentence);
      if (!nextWords) continue;
      if (picked.length && totalWords + nextWords > SPOKEN_SUMMARY_MAX_WORDS) break;
      picked.push(sentence);
      totalWords += nextWords;
      if (picked.length >= 2 || totalWords >= SPOKEN_SUMMARY_MAX_WORDS) break;
    }

    const summary = truncateWords((picked.join(" ") || clean).trim(), SPOKEN_SUMMARY_MAX_WORDS);
    if (!summary) return SPOKEN_SUMMARY_DETAIL_HINT;
    if (/\b(?:chat|details are in)\b/i.test(summary)) return summary;
    return `${summary} ${SPOKEN_SUMMARY_DETAIL_HINT}`;
  }

  function speechTextForReply(text: string, readFull: boolean) {
    return readFull ? cleanForSpeech(text, FULL_SPEECH_MAX_CHARS) : spokenSummaryFor(text);
  }

  function speakText(text: string, maxChars = 1200) {
    if (!voiceModeRef.current) return;
    const clean = cleanForSpeech(text, maxChars);
    if (!clean) return;
    window.speechSynthesis?.cancel();
    stopTtsAudio();
    wakeRecognitionRef.current?.stop?.();
    speakingRef.current = true;
    setSpeaking(true);
    const done = () => {
      speakingRef.current = false;
      setSpeaking(false);
      if (voiceModeRef.current) {
        // Keep the conversation open so the user can reply without saying "Sammy" again.
        markEngaged();
        window.setTimeout(() => startWakeRef.current?.(), 250);
      }
    };
    if (settings.elevenlabs_enabled && settings.elevenlabs_configured) {
      void speakViaElevenLabs(clean, done);
    } else {
      speakViaBrowser(clean, done);
    }
  }

  function speakViaBrowser(text: string, done: () => void) {
    const synth = window.speechSynthesis;
    if (!synth) {
      done();
      return;
    }
    const utterance = new SpeechSynthesisUtterance(text);
    const savedUri = window.localStorage.getItem(VOICE_URI_KEY) || "";
    const allVoices = synth.getVoices();
    const prefix = voicePrefixFor(lang);
    const saved = savedUri ? allVoices.find((voice) => voice.voiceURI === savedUri) : undefined;
    // Honor the saved voice as long as it speaks the current app language.
    const chosenVoice =
      (saved && voiceMatchesLang(saved, prefix) ? saved : undefined) ??
      pickDefaultCuteVoice(allVoices, prefix);
    if (chosenVoice) {
      utterance.voice = chosenVoice;
      utterance.lang = chosenVoice.lang;
    } else {
      utterance.lang = navigator.language || "en-US";
    }
    const savedRate = Number(window.localStorage.getItem(VOICE_RATE_KEY));
    utterance.rate = savedRate >= 0.5 && savedRate <= 1.5 ? savedRate : 1;
    utterance.pitch = CUTE_PITCH;
    utterance.onend = done;
    utterance.onerror = done;
    synth.speak(utterance);
  }

  async function speakViaElevenLabs(text: string, done: () => void) {
    try {
      const res = await fetch(`${API_BASE}/api/tts`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, voice_id: settings.elevenlabs_voice_id }),
      });
      if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`);
      if (!voiceModeRef.current) {
        done();
        return;
      }
      const url = URL.createObjectURL(await res.blob());
      const audio = new Audio(url);
      ttsAudioRef.current = audio;
      const finish = () => {
        URL.revokeObjectURL(url);
        if (ttsAudioRef.current === audio) ttsAudioRef.current = null;
        done();
      };
      audio.onended = finish;
      audio.onerror = finish;
      await audio.play();
    } catch (error) {
      // Fall back to the on-device voice so a reply is still spoken aloud.
      setStatus(t("Voice playback failed. {detail}", { detail: (error as Error).message }));
      speakViaBrowser(text, done);
    }
  }

  function stopTtsAudio() {
    const audio = ttsAudioRef.current;
    if (audio) {
      audio.pause();
      audio.src = "";
      ttsAudioRef.current = null;
    }
  }

  function stopSpeaking() {
    window.speechSynthesis?.cancel();
    stopTtsAudio();
    speakingRef.current = false;
    setSpeaking(false);
  }

  function toggleVoiceMode() {
    setVoiceMode((on) => {
      const next = !on;
      if (!next) stopSpeaking();
      return next;
    });
  }

  // Voice "engagement": after the first "Sammy …", the user can keep talking without repeating
  // the wake word; it re-arms after a stretch of silence (or when voice mode is turned off).
  function markEngaged() {
    engagedRef.current = true;
    if (engageTimerRef.current) window.clearTimeout(engageTimerRef.current);
    engageTimerRef.current = window.setTimeout(() => {
      engagedRef.current = false;
      engageTimerRef.current = null;
    }, ENGAGE_WINDOW_MS);
  }

  function clearEngagement() {
    engagedRef.current = false;
    if (engageTimerRef.current) {
      window.clearTimeout(engageTimerRef.current);
      engageTimerRef.current = null;
    }
  }

  function wakeGreetingText(forCommand: boolean) {
    const now = Date.now();
    const stored = Number(window.localStorage.getItem(WAKE_GREETING_STORAGE_KEY) || "0");
    const lastSpokenAt = wakeGreetingLastSpokenAtRef.current || (Number.isFinite(stored) ? stored : 0);
    if (lastSpokenAt && now - lastSpokenAt < WAKE_GREETING_RESET_MS) return "";
    wakeGreetingLastSpokenAtRef.current = now;
    window.localStorage.setItem(WAKE_GREETING_STORAGE_KEY, String(now));
    return t(forCommand ? "Hi, I'm on it." : "Hi, I'm here. Go ahead.");
  }

  const handleVoiceCommand = (command: string, acknowledgement = "") => {
    markEngaged();
    const text = command.charAt(0).toUpperCase() + command.slice(1);
    setStatus(t('Heard: "{text}"', { text }));
    pendingSpeakRef.current = true;
    pendingSpeakFullRef.current = wantsFullVoiceReadback(command);
    // Acknowledge out loud right away so Sammy feels responsive while the model works.
    keepSpeakingOnNextSendRef.current = true;
    speakText(acknowledgement || t(VOICE_ACKS[Math.floor(Math.random() * VOICE_ACKS.length)]));
    void sendMessage({ content: text, agent_id: selectedAgentId || agents[0]?.id || "default", voice: true });
  };

  voiceModeRef.current = voiceMode;
  generatingRef.current = generating;
  messagesRef.current = messages;
  handleVoiceCommandRef.current = handleVoiceCommand;
  voiceAuthActiveRef.current = Boolean(
    settings.voice_auth_enabled && settings.picovoice_access_key && settings.picovoice_speaker_profile
  );

  // Hands-free "Hey Sammy": continuous wake-word listening while voice mode is on.
  useEffect(() => {
    if (!voiceMode) {
      clearEngagement();
      const current = wakeRecognitionRef.current;
      wakeRecognitionRef.current = null;
      current?.stop?.();
      return;
    }
    const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      setStatus("Voice control isn't supported in this browser. Try Chrome or Safari.");
      setVoiceMode(false);
      return;
    }
    if (!window.isSecureContext) {
      setStatus("Voice control needs localhost or HTTPS — it won't work on the phone/LAN URL.");
      setVoiceMode(false);
      return;
    }
    let stopped = false;
    const start = () => {
      if (stopped || !voiceModeRef.current || speakingRef.current || wakeRecognitionRef.current) return;
      const recognition = new SpeechRecognition();
      recognition.lang = { en: "en-US", es: "es-ES", zh: "zh-CN" }[lang] || navigator.language || "en-US";
      recognition.continuous = true;
      recognition.interimResults = true;
      recognition.maxAlternatives = 1;
      recognition.onresult = (event: any) => {
        let interim = "";
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          const transcript = String(result[0]?.transcript || "");
          if (!result.isFinal) {
            interim += transcript;
            continue;
          }
          // With voice auth on, only accept speech that matched the enrolled owner recently.
          const ownerOk = () =>
            !voiceAuthActiveRef.current || Date.now() - ownerSpokeAtRef.current <= OWNER_RECENT_MS;
          const match = transcript.match(wakeWordRe(identityNameRef.current, langRef.current));
          if (match) {
            // Wake word heard — engage and run whatever followed it.
            const command = match[1].trim();
            if (!ownerOk()) {
              setStatus("Ignored — that didn't match your enrolled voice.");
              return;
            }
            markEngaged();
            const greeting = wakeGreetingText(Boolean(command));
            if (!command) {
              if (greeting) speakText(greeting);
              setStatus(t('Yes? Go ahead. No need to say "{name}" again.', { name: identityNameRef.current }));
            } else if (!generatingRef.current && !speakingRef.current) {
              handleVoiceCommandRef.current(command, greeting);
            }
            return;
          }
          if (engagedRef.current) {
            // Already in a conversation: treat the whole phrase as a command, no wake word needed.
            if (!ownerOk()) {
              setStatus("Ignored — that didn't match your enrolled voice.");
              return;
            }
            const command = transcript.trim();
            if (command && !generatingRef.current && !speakingRef.current) {
              handleVoiceCommandRef.current(command);
            }
            return;
          }
          if (transcript.trim()) {
            setStatus(`Heard "${transcript.trim()}" — start with "Sammy …" to send a command.`);
          }
        }
        if (interim.trim()) setStatus(`Listening… "${interim.trim()}"`);
      };
      recognition.onerror = (event: any) => {
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          setStatus("Sammy needs microphone access. Allow the mic for this site, then turn voice back on.");
          stopped = true;
          setVoiceMode(false);
        } else if (event.error === "audio-capture") {
          setStatus("No microphone found. Check your Mac's input device.");
          stopped = true;
          setVoiceMode(false);
        }
      };
      recognition.onend = () => {
        wakeRecognitionRef.current = null;
        if (!stopped && voiceModeRef.current && !speakingRef.current) {
          window.setTimeout(start, 300);
        }
      };
      wakeRecognitionRef.current = recognition;
      try {
        recognition.start();
      } catch {
        wakeRecognitionRef.current = null;
      }
    };
    startWakeRef.current = start;
    void (async () => {
      if (navigator.mediaDevices?.getUserMedia) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach((track) => track.stop());
        } catch (error) {
          setStatus(`Microphone access was blocked. ${(error as Error).message}`);
          setVoiceMode(false);
          return;
        }
      }
      if (!stopped && voiceModeRef.current) {
        start();
        setStatus(`Listening for "${identityNameRef.current}"…`);
      }
    })();
    return () => {
      stopped = true;
      const current = wakeRecognitionRef.current;
      wakeRecognitionRef.current = null;
      current?.stop?.();
    };
  }, [voiceMode]);

  // Voice authentication: while voice mode is on and an enrolled profile + key exist, run Eagle
  // speaker recognition on the mic and record when the owner was last heard (gates commands).
  useEffect(() => {
    const active =
      voiceMode &&
      settings.voice_auth_enabled &&
      Boolean(settings.picovoice_access_key) &&
      Boolean(settings.picovoice_speaker_profile);
    if (!active) return;
    let handle: VoiceAuthRecognizer | null = null;
    let cancelled = false;
    void (async () => {
      try {
        const { startRecognizer } = await import("./voiceAuth");
        const recognizer = await startRecognizer(
          settings.picovoice_access_key,
          settings.picovoice_speaker_profile,
          (score) => {
            if (score >= VOICE_AUTH_MATCH_THRESHOLD) ownerSpokeAtRef.current = Date.now();
          }
        );
        if (cancelled) void recognizer.stop();
        else handle = recognizer;
      } catch (error) {
        setStatus(`Voice authentication couldn't start. ${(error as Error).message}`);
      }
    })();
    return () => {
      cancelled = true;
      void handle?.stop();
    };
  }, [voiceMode, settings.voice_auth_enabled, settings.picovoice_access_key, settings.picovoice_speaker_profile]);

  // Read each voice-initiated reply aloud once it finishes generating.
  useEffect(() => {
    const was = prevGeneratingRef.current;
    prevGeneratingRef.current = generating;
    if (was && !generating && voiceModeRef.current && pendingSpeakRef.current) {
      pendingSpeakRef.current = false;
      const readFull = pendingSpeakFullRef.current;
      pendingSpeakFullRef.current = false;
      const lastAssistant = [...messagesRef.current].reverse().find((message) => message.role === "assistant");
      const responseStatus = lastAssistant?.metadata?.response_status;
      if (lastAssistant?.content && responseStatus !== "error" && responseStatus !== "stopped") {
        speakText(speechTextForReply(lastAssistant.content, readFull), readFull ? FULL_SPEECH_MAX_CHARS : 1200);
      }
    }
  }, [generating]);

  useEffect(
    () => () => {
      window.speechSynthesis?.cancel();
      stopTtsAudio();
      wakeRecognitionRef.current?.stop?.();
    },
    []
  );

  function mergeSavedAssistantMessage(streamId: string, saved: Message) {
    if (!saved?.id) return;
    setMessages((items) =>
      updateStreamingMessage(items, streamId, (message) => {
        const savedMetadata = saved.metadata ?? {};
        return {
          ...saved,
          content: saved.content || message.content,
          metadata: {
            ...savedMetadata,
            ...(savedMetadata.reasoning === undefined && message.metadata?.reasoning
              ? { reasoning: message.metadata.reasoning }
              : {}),
            ...(message.metadata?.progress_notes?.length ? { progress_notes: message.metadata.progress_notes } : {}),
            tool_events: message.metadata?.tool_events ?? [],
            response_status: savedMetadata.response_status ?? "complete",
          },
        };
      })
    );
  }

  function handleChatJobEvent(event: string, data: any, streamId: string, targetAgentId: string) {
    if (event === "conversation") {
      const conversation = data.conversation as Conversation;
      setSelectedConversationId(conversation.id);
      setSelectedConversationMode(conversation.mode === "tool_builder" ? "tool_builder" : "chat");
      setActiveJobConversationId(conversation.id);
      setSelectedAgentId(conversation.agent_id || targetAgentId);
      setAgentGroupCollapsed(conversation.agent_id || targetAgentId, false);
      setSelectedModel(resolveModelName(conversation.model || selectedModel, models));
    }
    if (event === "work_state") {
      setGenerationState(data as GenerationState);
      if (data.phase !== "reconnecting") setStatus("");
    }
    if (event === "token") {
      setMessages((items) =>
        updateStreamingMessage(items, streamId, (message) => ({
          ...message,
          content: message.content + data.content,
        }))
      );
    }
    if (event === "progress_note") {
      setMessages((items) =>
        updateStreamingMessage(items, streamId, (message) => ({
          ...message,
          content: "",
          metadata: {
            ...message.metadata,
            progress_notes: appendProgressNote(message.metadata?.progress_notes, data.content || message.content),
          },
        }))
      );
    }
    if (event === "tool_start" || event === "tool_result" || event === "memory_save") {
      const toolEvent: ToolEvent = {
        type: event === "memory_save" ? "memory" : event === "tool_start" ? "start" : "result",
        name: data.name,
        tool: data.tool,
        tool_display_name: data.tool_display_name,
        memory_file: data.memory_file,
        arguments: data.arguments,
        content: data.content,
        requires_reconnect: Boolean(data.requires_reconnect),
        received_at: Date.now(),
      };
      setMessages((items) =>
        updateStreamingMessage(items, streamId, (message) => {
          const preToolText = event === "tool_start" ? message.content.trim() : "";
          return {
            ...message,
            content: preToolText ? "" : message.content,
            metadata: {
              ...message.metadata,
              progress_notes: preToolText
                ? appendProgressNote(message.metadata?.progress_notes, preToolText)
                : message.metadata?.progress_notes,
              tool_events: [...(message.metadata?.tool_events ?? []), toolEvent],
            },
          };
        })
      );
      if (event === "tool_result" && data.requires_reconnect) {
        setStatus(`${data.tool_display_name || data.tool || "Tool"} needs reconnecting.`);
        void refreshTools();
      }
    }
    if (event === "reasoning") {
      setMessages((items) =>
        updateStreamingMessage(items, streamId, (message) => ({
          ...message,
          metadata: {
            ...message.metadata,
            reasoning: `${message.metadata?.reasoning ?? ""}${data.content}`,
          },
        }))
      );
    }
    if (event === "assistant_message" || event === "done" || event === "stopped") {
      const saved = (data.message ?? data) as Message;
      if (saved?.id) mergeSavedAssistantMessage(streamId, saved);
    }
    if (event === "status") setStatus(data.message);
    if (event === "error") {
      const errorMessage = data.message || "Sammy hit an error before finishing.";
      setStatus(errorMessage);
      markStreamingResponse(streamId, { response_status: "error", response_error: errorMessage });
    }
    return event === "done" || event === "stopped";
  }

  async function finishChatJob(jobId: string) {
    if (activeJobIdRef.current !== jobId) return;
    window.localStorage.removeItem(ACTIVE_CHAT_JOB_KEY);
    activeJobIdRef.current = "";
    lastJobEventIdRef.current = 0;
    streamingMessageIdRef.current = "";
    abortRef.current = null;
    setGenerating(false);
    setGenerationState(null);
    setActiveJobConversationId("");
    await Promise.all([refreshConversations(), refreshTools(), refreshAgents()]);
  }

  async function consumeChatJob(
    jobId: string,
    streamId: string,
    targetAgentId: string,
    initialAfter = 0
  ) {
    let after = initialAfter;
    let terminal = false;
    let reconnectDelay = 400;

    while (!terminal && activeJobIdRef.current === jobId) {
      const controller = new AbortController();
      abortRef.current = controller;
      let jobMissing = false;
      try {
        const response = await fetch(`${API_BASE}/api/chat/jobs/${jobId}/stream?after=${after}`, {
          credentials: "include",
          signal: controller.signal,
        });
        if (response.status === 404) jobMissing = true;
        if (!response.ok || !response.body) throw new Error(await response.text());

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        reconnectDelay = 400;

        const processBlock = (block: string) => {
          const parsed = parseSseBlock(block);
          if (!parsed) return;
          if (parsed.id && parsed.id <= after) return;
          if (parsed.id) {
            after = parsed.id;
            lastJobEventIdRef.current = parsed.id;
          }
          terminal = handleChatJobEvent(parsed.event, parsed.data, streamId, targetAgentId) || terminal;
        };

        while (!terminal) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const blocks = buffer.split("\n\n");
          buffer = blocks.pop() ?? "";
          blocks.forEach(processBlock);
        }
        if (buffer.trim()) processBlock(buffer);
      } catch (error) {
        if ((error as Error).name === "AbortError" && activeJobIdRef.current !== jobId) return;
        if (jobMissing) {
          const errorMessage = "The backend restarted and the active generation job could not be recovered.";
          setStatus(errorMessage);
          markStreamingResponse(streamId, { response_status: "error", response_error: errorMessage });
          terminal = true;
          break;
        }
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }

      if (terminal || activeJobIdRef.current !== jobId) break;

      try {
        const response = await fetch(`${API_BASE}/api/chat/jobs/${jobId}`, { credentials: "include" });
        if (response.status === 404) {
          const errorMessage = "The backend restarted and the active generation job could not be recovered.";
          setStatus(errorMessage);
          markStreamingResponse(streamId, { response_status: "error", response_error: errorMessage });
          terminal = true;
          break;
        }
        if (!response.ok) throw new Error(await response.text());
        const snapshot = ((await response.json()) as { job: ChatJobSnapshot }).job;
        if (snapshot.status === "complete" || snapshot.status === "error" || snapshot.status === "stopped") {
          if (snapshot.final_message) mergeSavedAssistantMessage(streamId, snapshot.final_message);
          if (snapshot.error) setStatus(snapshot.error);
          terminal = true;
          break;
        }
      } catch {
        // The job continues independently; retry the subscriber below.
      }

      setGenerationState({
        phase: "reconnecting",
        label: "Reconnecting to task",
        detail: "Sammy is still working in the background.",
        part: generationState?.part || 1,
        tool_step: generationState?.tool_step || 0,
      });
      await new Promise((resolve) => window.setTimeout(resolve, reconnectDelay));
      reconnectDelay = Math.min(4000, reconnectDelay * 2);
    }

    if (terminal) await finishChatJob(jobId);
  }

  async function resumeActiveChatJob(availableModels: ModelInfo[]) {
    if (activeJobIdRef.current) return;
    let snapshot: ChatJobSnapshot | undefined;
    const storedJobId = window.localStorage.getItem(ACTIVE_CHAT_JOB_KEY) || "";

    if (storedJobId) {
      try {
        const data = await api<{ job: ChatJobSnapshot }>(`/api/chat/jobs/${storedJobId}`);
        if (data.job.status === "queued" || data.job.status === "running") snapshot = data.job;
        else window.localStorage.removeItem(ACTIVE_CHAT_JOB_KEY);
      } catch {
        window.localStorage.removeItem(ACTIVE_CHAT_JOB_KEY);
      }
    }
    if (!snapshot) {
      try {
        const data = await api<{ jobs: ChatJobSnapshot[] }>("/api/chat/jobs/active");
        snapshot = data.jobs[0];
      } catch {
        return;
      }
    }
    if (!snapshot) return;

    const data = await api<{ conversation: Conversation; messages: Message[] }>(
      `/api/conversations/${snapshot.conversation_id}`
    );
    const anchorIndex = data.messages.findIndex((message) => message.id === snapshot?.user_message_id);
    const baseMessages = anchorIndex >= 0 ? data.messages.slice(0, anchorIndex + 1) : data.messages;
    const streamId = `stream-job-${snapshot.id}`;
    const localAssistant: Message = {
      id: streamId,
      role: "assistant",
      content: "",
      metadata: { tool_events: [], response_status: "streaming" },
      created_at: new Date().toISOString(),
    };

    activeJobIdRef.current = snapshot.id;
    streamingMessageIdRef.current = streamId;
    lastJobEventIdRef.current = 0;
    window.localStorage.setItem(ACTIVE_CHAT_JOB_KEY, snapshot.id);
    setSelectedConversationId(snapshot.conversation_id);
    setSelectedConversationMode(data.conversation.mode === "tool_builder" ? "tool_builder" : "chat");
    setActiveJobConversationId(snapshot.conversation_id);
    setSelectedAgentId(snapshot.agent_id);
    setSelectedModel(resolveModelName(snapshot.model, availableModels));
    setMessages([...baseMessages, localAssistant]);
    setGenerationState({
      phase: "reconnecting",
      label: "Rejoining active task",
      detail: "Replaying Sammy's work from the background job.",
      part: snapshot.part || 1,
      tool_step: snapshot.tool_step || 0,
    });
    setGenerating(true);
    void consumeChatJob(snapshot.id, streamId, snapshot.agent_id, 0);
  }

  async function sendMessage(override?: { content: string; regenerate_from?: string; agent_id?: string; voice?: boolean }) {
    // A just-spoken voice acknowledgement should keep playing instead of being cut off here.
    const keepSpeaking = keepSpeakingOnNextSendRef.current;
    keepSpeakingOnNextSendRef.current = false;
    const content = (override?.content ?? input).trim();
    if (!content || generating) return;
    setActivePage("chat");
    if (!keepSpeaking) {
      window.speechSynthesis?.cancel();
      stopTtsAudio();
    }
    if (!selectedConversationId && !override?.regenerate_from && !override?.agent_id && agents.length > 1) {
      openAgentPicker("send");
      return;
    }
    const targetAgentId = override?.agent_id || selectedAgentId || agents[0]?.id || "default";
    setInput("");
    setStatus("");
    setAttachments([]);
    setSelectedAgentId(targetAgentId);

    const streamId = `stream-${Date.now()}`;
    const localUser: Message = {
      id: `local-user-${Date.now()}`,
      role: "user",
      content,
      metadata: {},
      created_at: new Date().toISOString(),
    };
    const localAssistant: Message = {
      id: streamId,
      role: "assistant",
      content: "",
      metadata: { tool_events: [], response_status: "streaming" },
      created_at: new Date().toISOString(),
    };

    if (!override?.regenerate_from) {
      setMessages((items) => [...items, localUser, localAssistant]);
    } else {
      const index = messages.findIndex((message) => message.id === override.regenerate_from);
      setMessages((items) => [...items.slice(0, index + 1), localAssistant]);
    }

    const controller = new AbortController();
    abortRef.current = controller;
    streamingMessageIdRef.current = streamId;
    setGenerationState({ phase: "starting", label: "Starting task", detail: "Preparing the background job.", part: 1, tool_step: 0 });
    setGenerating(true);

    try {
      const response = await fetch(`${API_BASE}/api/chat/jobs`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          conversation_id: selectedConversationId || null,
          message: content,
          model: selectedModel,
          agent_id: targetAgentId,
          options: selectedOptions(),
          attachments,
          regenerate_from: override?.regenerate_from,
          voice: Boolean(override?.voice),
        }),
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await response.text());
      const created = (await response.json()) as ChatJobCreateResponse;
      const jobId = created.job.id;

      activeJobIdRef.current = jobId;
      lastJobEventIdRef.current = 0;
      setSelectedConversationId(created.conversation.id);
      setSelectedConversationMode(created.conversation.mode === "tool_builder" ? "tool_builder" : "chat");
      setActiveJobConversationId(created.conversation.id);
      setSelectedAgentId(created.conversation.agent_id || targetAgentId);
      setSelectedModel(resolveModelName(created.conversation.model || selectedModel, models));
      setAgentGroupCollapsed(created.conversation.agent_id || targetAgentId, false);
      window.localStorage.setItem(ACTIVE_CHAT_JOB_KEY, jobId);
      await consumeChatJob(jobId, streamId, targetAgentId, 0);
    } catch (error) {
      if ((error as Error).name === "AbortError") return;
      const errorMessage = (error as Error).message;
      setStatus(errorMessage);
      markStreamingResponse(streamId, { response_status: "error", response_error: errorMessage });
      setGenerating(false);
      setGenerationState(null);
      streamingMessageIdRef.current = "";
    } finally {
      if (!activeJobIdRef.current) abortRef.current = null;
    }
  }

  function stopGeneration() {
    pendingSpeakRef.current = false;
    pendingSpeakFullRef.current = false;
    window.speechSynthesis?.cancel();
    stopTtsAudio();
    const jobId = activeJobIdRef.current;
    if (!jobId) {
      abortRef.current?.abort();
      markStreamingResponse(streamingMessageIdRef.current, {
        response_status: "stopped",
        response_notice: "Generation was stopped before the background job started.",
      });
      setGenerating(false);
      setGenerationState(null);
      return;
    }
    setGenerationState((current) => ({
      phase: "stopping",
      label: "Stopping",
      detail: "Sammy will stop after any tool call already in progress returns.",
      part: current?.part || 1,
      tool_step: current?.tool_step || 0,
    }));
    void api<{ job: ChatJobSnapshot }>(`/api/chat/jobs/${jobId}/stop`, { method: "POST" }).catch((error) => {
      setStatus(`Could not stop the task. ${(error as Error).message}`);
    });
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void sendMessage();
    }
  }

  async function handleFileUpload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;
    const body = new FormData();
    body.append("file", file);
    const response = await fetch(`${API_BASE}/api/files/upload`, { method: "POST", credentials: "include", body });
    if (!response.ok) {
      setStatus(await response.text());
      return;
    }
    const uploaded = await response.json();
    setAttachments((items) => [...items, uploaded.id]);
    setInput((current) => `${current}${current ? "\n" : ""}Attached: ${uploaded.filename}`);
  }

  function copy(content: string) {
    void navigator.clipboard.writeText(content);
  }

  function exportConversation() {
    if (!selectedConversationId) return;
    window.open(`${API_BASE}/api/conversations/${selectedConversationId}/export`, "_blank");
  }

  function regenerateFromMessage(message: Message) {
    setActiveUserActionId("");
    void sendMessage({ content: message.content, regenerate_from: message.id });
  }

  // The active "bestie" drives the whole app's identity (logo, name, greeting, wake word).
  // Empty/unknown active_bestie_id falls back to the built-in Sammy.
  const activeBestie = useMemo(
    () => besties.find((bestie) => bestie.id === settings.active_bestie_id) ?? null,
    [besties, settings.active_bestie_id]
  );
  const identityName = activeBestie?.name || "Sammy";
  const identityAvatarUrl = activeBestie?.avatar ? `${API_BASE}/api/files/${activeBestie.avatar}` : SAMMY_LOGO;
  // Keep the latest identity name reachable from the wake-word recognition handler (which lives
  // in a long-lived effect and otherwise can't see the current render's value).
  identityNameRef.current = identityName;
  langRef.current = lang;
  // The built-in "default" agent is Sammy itself, so it reflects the active identity; named
  // work agents (e.g. Email Manager) keep their own label.
  const selectedAgentName =
    selectedAgent && selectedAgent.id !== "default" ? selectedAgent.name : identityName;
  // Mobile-connection link shown in the header popup. Prefer Sammy's own mDNS alias
  // (e.g. sammy.local), then the Mac's .local name, then the raw LAN IP, then the current
  // origin (already correct when opened from a phone).
  const networkFallbackOrigin = typeof window !== "undefined" ? window.location.origin : "";
  const networkPrimaryUrl =
    networkInfo?.alias_url || networkInfo?.local_url || networkInfo?.lan_url || networkFallbackOrigin;
  const networkSecondaryUrl =
    networkInfo && networkPrimaryUrl !== networkInfo.lan_url ? networkInfo.lan_url : "";
  // "From any network" link — prefer the HTTPS (Tailscale serve) URL, else the plain tailnet URL.
  const networkTailnetUrl = networkInfo?.tailscale_https_url || networkInfo?.tailscale_url || "";
  const networkTailnetSecure = Boolean(networkInfo?.tailscale_https_url);
  const copyNetworkLink = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      setNetworkCopied(url);
      window.setTimeout(() => setNetworkCopied(""), 1500);
    } catch {
      /* clipboard blocked — the link is still visible to copy by hand */
    }
  };
  const agentById = useMemo(() => new Map(agents.map((agent) => [agent.id, agent])), [agents]);
  const conversationGroups = useMemo(() => groupConversationsByAgent(conversations, agents), [conversations, agents]);
  const browserHost = typeof window === "undefined" ? "" : window.location.hostname;
  const isNetworkAccess = Boolean(browserHost && !["localhost", "127.0.0.1", "::1"].includes(browserHost));
  const isUnprotectedNetworkAccess = isNetworkAccess && !authStatus?.password_required;

  if (!authStatus) {
    return (
      <div className="flex h-full items-center justify-center bg-[var(--canvas)] text-sm text-[var(--muted)]">
        {t("Loading Sammy...")}
      </div>
    );
  }

  if (authStatus.password_required && !authStatus.authenticated) {
    return (
      <LoginScreen
        password={loginPassword}
        error={loginError}
        submitting={loginSubmitting}
        onPasswordChange={setLoginPassword}
        onSubmit={submitLogin}
      />
    );
  }

  return (
    <IdentityContext.Provider value={{ name: identityName, avatarUrl: identityAvatarUrl }}>
    <div className="flex h-full overflow-hidden bg-[var(--canvas)] text-[var(--ink)]">
      <aside
        className={[
          "hairline-right hidden shrink-0 bg-[var(--side)] md:flex md:flex-col",
          sidebarCollapsed ? "w-14" : "w-[292px]",
        ].join(" ")}
      >
        {sidebarCollapsed ? (
          <>
            <div className="flex h-14 items-center justify-start px-2">
              <button
                type="button"
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                onClick={() => setSidebarCollapsed(false)}
                title={t("Show sidebar")}
              >
                <PanelLeftOpen size={17} />
              </button>
            </div>
            <div className="flex items-center justify-center px-2 pt-1">
              <button
                type="button"
                onClick={openToolsPage}
                aria-current={activePage === "tools" ? "page" : undefined}
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                title={t("Tools")}
              >
                <Wrench size={17} />
              </button>
            </div>
            <div className="flex items-center justify-center px-2 pb-4 pt-1">
              <button
                type="button"
                onClick={() => openAgentPicker("new")}
                className="flex h-10 w-10 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                title={t("New chat")}
              >
                <Plus size={18} />
              </button>
            </div>

            <div className="scrollbar flex-1 overflow-y-auto px-2 pb-3">
              <div className="flex flex-col items-center gap-1.5">
                {conversationGroups.flatMap((group) => (collapsedAgentGroups[group.key] ? [] : group.items)).map((conversation) => (
                    <button
                      key={conversation.id}
                      type="button"
                      className={[
                        "flex h-10 w-10 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]",
                        activePage === "chat" && selectedConversationId === conversation.id ? "bg-[var(--surface)] text-[var(--ink)] shadow-lift" : "",
                      ].join(" ")}
                      title={conversation.title}
                      onClick={() => openConversation(conversation.id)}
                    >
                      <AgentAvatar
                        agent={agentById.get(conversation.agent_id ?? "")}
                        iconSize={17}
                        className="flex h-9 w-9 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)]"
                      />
                    </button>
                  ))}
              </div>
            </div>

            <div className="p-2">
              <button
                type="button"
                className="flex h-10 w-10 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                onClick={() => openSettings()}
                title={t("Settings")}
              >
                <Settings size={17} />
              </button>
            </div>
          </>
        ) : (
          <>
            <div className="flex h-14 items-center justify-between px-3">
              <div className="flex min-w-0 items-center">
                <span className="font-display text-lg font-semibold tracking-tight">{identityName}</span>
              </div>
              <button
                type="button"
                className="flex h-9 w-9 items-center justify-center rounded-lg text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                onClick={() => setSidebarCollapsed(true)}
                title={t("Hide sidebar")}
              >
                <PanelLeftClose size={17} />
              </button>
            </div>
            <div className="flex flex-col gap-2 px-3 pb-5 pt-1">
              <button
                type="button"
                onClick={openToolsPage}
                aria-current={activePage === "tools" ? "page" : undefined}
                className={[
                  "flex h-10 w-full items-center justify-center gap-2 rounded-xl border bg-[var(--bright-action)] text-sm font-semibold text-[var(--bright-action-ink)] shadow-lift transition-colors hover:bg-[var(--bright-action-hover)]",
                  activePage === "tools"
                    ? "border-[var(--accent)]"
                    : "border-[var(--line)]",
                ].join(" ")}
              >
                <Wrench size={16} />
                {t("Tools")}
              </button>
              <button
                type="button"
                onClick={() => openAgentPicker("new")}
                className="flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-[var(--accent)] text-sm font-semibold text-[var(--accent-ink)] shadow-lift hover:brightness-105"
              >
                <Plus size={16} />
                {t("New Chat")}
              </button>
            </div>

            <div className="scrollbar flex-1 overflow-y-auto px-2 pb-3">
              {conversationGroups.map((group) => {
                const collapsed = Boolean(collapsedAgentGroups[group.key]);
                return (
                  <section key={group.key} className="mb-4">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-[0.68rem] uppercase text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                      aria-expanded={!collapsed}
                      onClick={() => toggleAgentGroup(group.key)}
                    >
                      <ChevronDown
                        size={13}
                        className={["shrink-0", collapsed ? "-rotate-90" : ""].join(" ")}
                      />
                      <AgentAvatar
                        agent={group.agent}
                        iconSize={12}
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)]"
                      />
                      <span className="min-w-0 flex-1 truncate">{t(group.label)}</span>
                      <span>{group.items.length}</span>
                    </button>
                    {!collapsed ? (
                      <div className="mt-1 space-y-1.5">
                        {group.items.map((conversation) => (
                            <div
                              key={conversation.id}
                              className={`group flex items-center gap-2 rounded-lg border px-2 py-2 ${
                                activePage === "chat" && selectedConversationId === conversation.id
                                  ? "border-[var(--line)] bg-[var(--surface-2)] text-[var(--ink)]"
                                  : "border-transparent hover:bg-[var(--surface-2)]/60 hover:text-[var(--ink)]"
                              }`}
                            >
                              <AgentAvatar
                                agent={agentById.get(conversation.agent_id ?? "")}
                                iconSize={15}
                                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)]"
                              />
                              <button
                                type="button"
                                className="min-w-0 flex-1 text-left"
                                onClick={() => openConversation(conversation.id)}
                              >
                                <div className="truncate text-sm">{conversation.title}</div>
                                <div className="truncate font-mono text-[0.68rem] text-[var(--muted)]">
                                  {conversation.preview || resolveModelName(conversation.model, models)}
                                </div>
                              </button>
                              <button
                                type="button"
                                className="rounded-full p-1 opacity-0 hover:bg-[var(--surface-3)] group-hover:opacity-100"
                                title={conversation.pinned ? t("Unpin") : t("Pin")}
                                onClick={() => togglePin(conversation)}
                              >
                                {conversation.pinned ? <PinOff size={13} /> : <Pin size={13} />}
                              </button>
                              <button
                                type="button"
                                className="rounded-full p-1 opacity-0 hover:bg-[var(--surface-3)] group-hover:opacity-100"
                                title={t("Delete")}
                                onClick={() => deleteConversation(conversation.id)}
                              >
                                <Trash2 size={13} />
                              </button>
                            </div>
                          ))}
                      </div>
                    ) : null}
                  </section>
                );
              })}
            </div>

            <div className="border-t border-[var(--separator)] p-2.5">
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-full px-3 py-2 text-sm text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                onClick={() => openSettings()}
              >
                <Settings size={16} />
                {t("Settings")}
              </button>
            </div>
          </>
        )}
      </aside>

      {mobileNavOpen && (
        <div className="fixed inset-0 z-50 flex flex-col bg-[var(--side)] pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-[env(safe-area-inset-top)] text-[var(--ink)] md:hidden">
          <div className="hairline-bottom flex h-16 shrink-0 items-center justify-between gap-3 bg-[var(--surface)] px-3">
            <div className="flex min-w-0 items-center gap-2">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-[var(--surface-2)]">
                <SammyLogo className="h-9 w-9" src={identityAvatarUrl} alt={identityName} />
              </span>
              <span className="truncate text-base font-semibold">{t("Chats")}</span>
            </div>
            <button
              type="button"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)] hover:bg-[var(--surface-3)] hover:text-[var(--ink)]"
              onClick={() => setMobileNavOpen(false)}
              title={t("Close chats")}
              aria-label={t("Close chats")}
            >
              <X size={18} />
            </button>
          </div>

          <div className="grid gap-2 px-3 py-3">
            <button
              type="button"
              data-testid="mobile-tools-nav"
              onClick={openToolsPage}
              aria-current={activePage === "tools" ? "page" : undefined}
              className={[
                "flex h-11 w-full items-center justify-center gap-2 rounded-full border bg-[var(--bright-action)] text-sm font-medium text-[var(--bright-action-ink)] shadow-lift transition-colors hover:bg-[var(--bright-action-hover)]",
                activePage === "tools"
                  ? "border-[var(--accent)]"
                  : "border-[var(--line)]",
              ].join(" ")}
            >
              <Wrench size={16} />
              {t("Tools")}
            </button>
            <button
              type="button"
              onClick={() => void createMobileChat()}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface)] text-sm font-medium shadow-lift hover:bg-[var(--inset)]"
            >
              <Plus size={16} />
              {t("New Chat")}
            </button>
          </div>

          <div className="scrollbar flex-1 overflow-y-auto px-3 pb-4">
            {conversationGroups.length === 0 ? (
              <div className="rounded-xl border border-[var(--line)] bg-[var(--surface)] px-4 py-8 text-center text-sm text-[var(--muted)]">
                {t("No chats yet")}
              </div>
            ) : (
              conversationGroups.map((group) => {
                const collapsed = Boolean(collapsedAgentGroups[group.key]);
                return (
                  <section key={group.key} className="mb-5">
                    <button
                      type="button"
                      className="flex w-full items-center gap-2 rounded-lg px-1 py-2 text-left text-[0.68rem] uppercase text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                      aria-expanded={!collapsed}
                      onClick={() => toggleAgentGroup(group.key)}
                    >
                      <ChevronDown
                        size={13}
                        className={["shrink-0", collapsed ? "-rotate-90" : ""].join(" ")}
                      />
                      <AgentAvatar
                        agent={group.agent}
                        iconSize={13}
                        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)]"
                      />
                      <span className="min-w-0 flex-1 truncate">{t(group.label)}</span>
                      <span>{group.items.length}</span>
                    </button>
                    {!collapsed ? (
                      <div className="mt-1 space-y-2">
                        {group.items.map((conversation) => (
                            <div
                              key={conversation.id}
                              className={[
                                "flex items-center gap-2 rounded-xl border px-2.5 py-2.5",
                                activePage === "chat" && selectedConversationId === conversation.id
                                  ? "border-[var(--line)] bg-[var(--surface)] shadow-lift"
                                  : "border-transparent bg-[var(--surface)]",
                              ].join(" ")}
                            >
                              <AgentAvatar
                                agent={agentById.get(conversation.agent_id ?? "")}
                                iconSize={16}
                                className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-[var(--surface-2)] text-[var(--muted)]"
                              />
                              <button
                                type="button"
                                className="min-w-0 flex-1 text-left"
                                onClick={() => void openMobileConversation(conversation.id)}
                              >
                                <div className="truncate text-sm font-medium">{conversation.title}</div>
                                <div className="truncate font-mono text-[0.68rem] text-[var(--muted)]">
                                  {conversation.preview || resolveModelName(conversation.model, models)}
                                </div>
                              </button>
                              <button
                                type="button"
                                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-3)] hover:text-[var(--ink)]"
                                title={conversation.pinned ? t("Unpin") : t("Pin")}
                                aria-label={conversation.pinned ? t("Unpin") : t("Pin")}
                                onClick={() => void togglePin(conversation)}
                              >
                                {conversation.pinned ? <PinOff size={14} /> : <Pin size={14} />}
                              </button>
                              <button
                                type="button"
                                className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface-3)] hover:text-[var(--ink)]"
                                title={t("Delete")}
                                aria-label={t("Delete")}
                                onClick={() => void deleteConversation(conversation.id)}
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          ))}
                      </div>
                    ) : null}
                  </section>
                );
              })
            )}
          </div>

          <div className="border-t border-[var(--separator)] px-3 pt-2">
            <button
              type="button"
              className="flex h-11 w-full items-center gap-2 rounded-full px-3 text-sm text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
              onClick={() => {
                setMobileNavOpen(false);
                openSettings();
              }}
            >
              <Settings size={16} />
              {t("Settings")}
            </button>
          </div>
        </div>
      )}

      <main className="flex min-w-0 flex-1 flex-col">
        <header className="hairline-bottom grid h-14 shrink-0 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-1.5 overflow-hidden bg-[var(--surface)] px-2 sm:h-16 sm:gap-2 sm:px-3">
          <div className="flex min-w-0 items-center gap-1 justify-self-start">
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-full border-0 bg-[var(--surface-2)] text-[var(--muted)] hover:bg-[var(--surface-3)] hover:text-[var(--ink)] sm:h-10 sm:w-10 md:hidden"
              onClick={() => setMobileNavOpen(true)}
              title={t("Chats")}
              aria-label={t("Chats")}
            >
              <Plus size={17} />
            </button>
          </div>

          <div className="flex min-w-0 max-w-[45vw] items-center justify-center justify-self-center px-3 py-1 text-center md:hidden">
            <span className="truncate text-sm font-semibold text-[var(--ink)]">{selectedAgentName}</span>
          </div>

          <button
            type="button"
            className="group hidden min-w-0 max-w-[45vw] items-center justify-center gap-1.5 justify-self-center rounded-full px-3 py-1 text-center hover:bg-[var(--surface-2)] md:flex"
            title={t("Open on your phone")}
            onClick={openNetworkPopup}
          >
            <span className="truncate text-sm font-semibold text-[var(--ink)] sm:text-base">{selectedAgentName}</span>
            <Smartphone size={14} className="shrink-0 text-[var(--muted)] group-hover:text-[var(--ink)]" />
          </button>

          <div className="flex shrink-0 items-center gap-1.5 justify-self-end">
            {activePage === "chat" ? (
              <button
                type="button"
                className="hidden h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--line)] bg-[var(--surface)] text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)] lg:flex"
                onClick={exportConversation}
                title={t("Export conversation")}
              >
                <Download size={15} />
              </button>
            ) : null}
            <button
              type="button"
              className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-transparent p-0.5 transition-colors hover:bg-[var(--surface-2)] sm:h-10 sm:w-10"
              onClick={openBestiePage}
              aria-current={bestieOpen ? "page" : undefined}
              title={t("My Bestie")}
            >
              <SammyLogo className="h-8 w-8 sm:h-9 sm:w-9" withShadow={false} src={identityAvatarUrl} alt={identityName} />
            </button>
          </div>
        </header>

        {isUnprotectedNetworkAccess ? (
          <div className="shrink-0 border-b border-[var(--red)] bg-[var(--surface)] px-3 py-2 text-xs leading-relaxed text-[var(--red)] sm:text-sm">
            {t("Network access is active on {host}, but Sammy is not password protected yet. Only use this on a trusted private network.", { host: browserHost })}
          </div>
        ) : null}

        {activePage === "tools" ? (
          <ToolsPage
            tools={tools}
            activeToolNames={activeToolNames}
            agentName={selectedAgentName}
            onToggle={setSelectedAgentPlugin}
            onManage={() => openSettings("tools")}
            onCreateTool={() => void startToolBuilder()}
          />
        ) : (
          <>
        <section className="scrollbar min-h-0 flex-1 overflow-y-auto py-4 sm:py-6">
          {messages.length === 0 ? (
            selectedConversationMode === "tool_builder" ? (
              <ToolBuilderIntro agentName={selectedAgentName} />
            ) : (
              <EmptyState model={selectedModel} />
            )
          ) : (
            <div className="space-y-6">
              {selectedConversationMode === "tool_builder" ? (
                <ToolBuilderIntro agentName={selectedAgentName} compact />
              ) : null}
              {messages.map((message) => (
                <MessageBubble
                  key={message.id}
                  message={message}
                  onCopy={copy}
                  onRegenerate={regenerateFromMessage}
                  activeActionMessageId={activeUserActionId}
                  onActivateActions={setActiveUserActionId}
                  generating={generating}
                />
              ))}
              {generating && generationState && (!activeJobConversationId || selectedConversationId === activeJobConversationId) ? (
                <GenerationLedger state={generationState} />
              ) : null}
              <div ref={bottomRef} />
            </div>
          )}
        </section>

        <footer className="shrink-0 bg-[var(--canvas)] px-7 pb-[max(0.625rem,env(safe-area-inset-bottom))] pt-1.5 sm:px-3 sm:pb-[max(0.75rem,env(safe-area-inset-bottom))] sm:pt-2">
          <div className="mx-auto max-w-4xl">
            <div className="relative rounded-xl border border-[var(--line)] bg-[var(--surface)] px-2 pb-1 pt-1.5 sm:px-3 sm:pb-2 sm:pt-2.5">
              {selectedConversationMode === "tool_builder" ? (
                <div data-testid="tool-build-mode-composer" className="mb-1.5 flex items-center gap-2 border-b border-[var(--separator)] px-1 pb-1.5 sm:mb-2 sm:pb-2">
                  <Wrench size={14} className="shrink-0 text-[var(--accent)]" />
                  <span className="text-xs font-semibold text-[var(--ink)]">{t("Tool Build Mode")}</span>
                  <span className="ml-auto truncate font-mono text-[0.64rem] text-[var(--muted)]">
                    {t("Building for {agent}", { agent: selectedAgentName })}
                  </span>
                </div>
              ) : null}
              <textarea
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                rows={1}
                placeholder={
                  listening
                    ? t("Listening...")
                    : selectedConversationMode === "tool_builder"
                      ? t("Describe the tool you want Sammy to build...")
                      : t("Ask Sammy...")
                }
                className="composer-input max-h-16 min-h-7 w-full resize-y border-0 bg-transparent px-1 text-[0.95rem] leading-5 outline-none placeholder:text-[var(--muted)] sm:max-h-24 sm:min-h-9 sm:text-sm"
              />

              <div className="mt-1 flex items-center gap-1 sm:mt-2 sm:justify-between sm:gap-2">
                <div className="flex min-w-0 flex-1 items-center gap-1 sm:flex-none sm:flex-wrap sm:gap-2">
                  <input ref={fileInputRef} type="file" className="hidden" onChange={handleFileUpload} />
                  <button
                    type="button"
                    className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[var(--muted)] hover:bg-[var(--surface)] hover:text-[var(--ink)]"
                    onClick={() => fileInputRef.current?.click()}
                    title={t("Attach file")}
                  >
                    <FileUp size={16} />
                  </button>

                  <button
                    ref={pluginMenuButtonRef}
                    type="button"
                    className={[
                      "relative inline-flex h-8 min-w-10 shrink-0 items-center justify-center gap-1 rounded-lg border px-1.5 font-mono text-[0.72rem] sm:min-w-11 sm:gap-1.5 sm:px-2 sm:text-sm",
                      pluginMenuOpen
                        ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)]"
                        : disconnectedActiveTools.length
                          ? "border-[var(--red)] bg-[var(--surface)] text-[var(--red)] hover:bg-[var(--surface-2)]"
                        : "border-transparent text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
                    ].join(" ")}
                    title={activePluginFullLabel}
                    aria-label={activePluginFullLabel}
                    onClick={() => {
                      setPluginMenuOpen((open) => !open);
                      setPluginMenuView("active");
                      setReasoningMenuOpen(false);
                    }}
                  >
                    <Plug size={16} />
                    <span>{activePluginCountLabel}</span>
                    {disconnectedActiveTools.length ? (
                      <span className="absolute -right-0.5 -top-0.5 flex h-3.5 min-w-3.5 items-center justify-center rounded-full bg-[var(--red)] px-0.5 text-[0.55rem] font-bold leading-none text-white">
                        !
                      </span>
                    ) : null}
                  </button>

                  {attachments.length > 0 && (
                    <span className="hidden shrink-0 rounded-full border border-[var(--line)] px-2.5 py-1 font-mono text-xs text-[var(--muted)] sm:inline-flex">
                      {t("{n} attached", { n: attachments.length })}
                    </span>
                  )}
                </div>

                <div className="flex shrink-0 items-center gap-1 sm:gap-2">
                  <div className="relative">
                    <button
                      ref={reasoningMenuButtonRef}
                      type="button"
                      className={[
                        "inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border px-0 font-mono text-[0.72rem] sm:w-auto sm:gap-2 sm:px-2.5 sm:text-sm",
                        reasoningMenuOpen
                          ? "border-[var(--accent)] bg-[var(--accent-soft)] text-[var(--ink)]"
                          : "border-transparent text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
                      ].join(" ")}
                      title={settings.think ? t("Reasoning") : t("Normal")}
                      aria-label={settings.think ? t("Reasoning") : t("Normal")}
                      onClick={() => {
                        setReasoningMenuOpen((open) => !open);
                        setPluginMenuOpen(false);
                      }}
                    >
                      <SlidersHorizontal size={14} />
                      <span className="hidden sm:inline">{settings.think ? t("Reasoning") : t("Normal")}</span>
                      <ChevronDown size={13} className="hidden sm:block" />
                    </button>

                    {reasoningMenuOpen && (
                      <div
                        ref={reasoningMenuRef}
                        className="absolute bottom-12 right-0 z-30 w-48 rounded-xl border border-[var(--line)] bg-[var(--surface)] p-1.5 shadow-popover"
                      >
                        {[
                          ["Normal", false],
                          ["Reasoning", true],
                        ].map(([label, think]) => {
                          const active = settings.think === think;
                          return (
                            <button
                              key={label as string}
                              type="button"
                              className={[
                                "flex w-full items-center justify-between rounded-lg px-3 py-2 text-left text-sm",
                                active ? "bg-[var(--accent-soft)] text-[var(--ink)]" : "text-[var(--muted)] hover:bg-[var(--surface-2)]",
                              ].join(" ")}
                              onClick={() => void setReasoningMode(Boolean(think))}
                            >
                              <span>{t(label as string)}</span>
                              {active ? <Circle size={9} className="fill-[var(--green)] text-[var(--green)]" /> : null}
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>

                  {/* Desktop: hands-free wake-word voice mode (continuous recognition isn't
                      reliable on mobile, so it's hidden there). */}
                  <button
                    type="button"
                    className={[
                      "hidden h-9 w-9 items-center justify-center rounded-lg sm:h-8 sm:w-8 md:flex",
                      voiceMode
                        ? "bg-[var(--accent)] text-[var(--accent-ink)]"
                        : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
                      speaking ? "pulse-dot" : "",
                      !voiceSupported ? "opacity-60" : "",
                    ].join(" ")}
                    onClick={toggleVoiceMode}
                    title={
                      voiceMode
                        ? speaking
                          ? t("Sammy is speaking")
                          : t('Hands-free is on — say "Sammy …"')
                        : t('Hands-free voice — say "Sammy …"')
                    }
                    aria-pressed={voiceMode}
                  >
                    {speaking ? <Volume2 size={16} /> : <AudioLines size={16} />}
                  </button>

                  {/* Mobile: tap-to-talk dictation. Always shown on mobile; if the browser/context
                      can't do it (e.g. insecure HTTP, or no recognition API), tapping explains why. */}
                  <button
                    type="button"
                    className={[
                      "flex h-8 w-8 items-center justify-center rounded-lg md:hidden",
                      listening
                        ? "bg-[var(--accent-soft)] text-[var(--green)]"
                        : "text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
                      !voiceSupported ? "opacity-60" : "",
                    ].join(" ")}
                    onClick={toggleVoiceInput}
                    title={listening ? t("Stop voice input") : t("Voice input")}
                  >
                    {listening ? <MicOff size={16} /> : <Mic size={16} />}
                  </button>

                  {generating ? (
                    <button
                      type="button"
                      className="flex h-8 w-8 items-center justify-center rounded-lg border border-[var(--line-strong)] bg-[var(--surface)] text-[var(--ink)] hover:bg-[var(--inset)]"
                      onClick={stopGeneration}
                      title={t("Stop")}
                    >
                      <Square size={15} />
                    </button>
                  ) : (
                    <button
                      type="button"
                      className="flex h-8 w-8 items-center justify-center rounded-lg bg-[var(--accent)] text-[var(--accent-ink)] disabled:opacity-45"
                      disabled={!input.trim()}
                      onClick={() => sendMessage()}
                      title={t("Send")}
                    >
                      <Send size={16} />
                    </button>
                  )}
                </div>
              </div>

              {pluginMenuOpen && (
                <div
                  ref={pluginMenuRef}
                  className="absolute bottom-16 left-1/2 z-30 w-[calc(100vw-1.5rem)] max-w-[300px] -translate-x-1/2 rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-2 shadow-popover sm:left-4 sm:w-[min(300px,calc(100vw-2rem))] sm:translate-x-0"
                >
                  <div className="mb-1 flex items-center justify-between px-2 py-1">
                    <span className="font-mono text-[0.68rem] uppercase text-[var(--muted)]">
                      {pluginMenuView === "active" ? t("Active tools") : t("Add tool")}
                    </span>
                    {pluginMenuView === "add" ? (
                      <button
                        type="button"
                        className="rounded-full px-2 py-1 font-mono text-xs text-[var(--muted)] hover:bg-[var(--surface-2)]"
                        onClick={() => setPluginMenuView("active")}
                      >
                        {t("Back")}
                      </button>
                    ) : null}
                  </div>

                  <div className="max-h-72 overflow-y-auto pr-1 scrollbar">
                    {(pluginMenuView === "active" ? activeTools : inactiveTools).map((tool) => {
                      const reconnectNeeded = needsPluginReconnect(tool);
                      const canReconnect = hasSavedOAuthClient(tool);
                      const active = pluginMenuView === "active";
                      return (
                        <div
                          key={tool.name}
                          className="flex items-center justify-between gap-2 rounded-xl px-2 py-2 hover:bg-[var(--surface-2)]"
                        >
                          <span className="flex min-w-0 items-center gap-2">
                            {reconnectNeeded ? (
                              <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[var(--red)] text-[0.62rem] font-bold leading-none text-white">
                                !
                              </span>
                            ) : (
                              <Circle
                                size={9}
                                style={{ color: tool.plugin?.brand_color || undefined, fill: tool.plugin?.brand_color || undefined }}
                                className={!tool.plugin?.brand_color && tool.connected ? "fill-[var(--green)] text-[var(--green)]" : "text-[var(--muted)]"}
                              />
                            )}
                            <span className="min-w-0">
                              <span className="block truncate text-sm">{tool.display_name}</span>
                            </span>
                          </span>
                          <span className="flex shrink-0 items-center gap-2">
                            {reconnectNeeded ? (
                              <button
                                type="button"
                                className="flex h-8 shrink-0 items-center justify-center gap-1 rounded-full border border-[var(--red)] bg-[var(--surface)] px-2 text-[var(--red)] hover:bg-[var(--surface-3)]"
                                onClick={() => connectPluginOAuth(tool)}
                                title={canReconnect ? t("Reconnect tool") : t("Add OAuth credentials")}
                              >
                                {canReconnect ? <RefreshCcw size={14} /> : <Settings size={14} />}
                                <span className="hidden text-xs sm:inline">{canReconnect ? t("Reconnect") : t("Setup")}</span>
                              </button>
                            ) : null}
                            <label className="relative inline-flex cursor-pointer items-center">
                              <input
                                type="checkbox"
                                checked={active}
                                onChange={() => setSelectedAgentPlugin(tool.name, !active)}
                                className="peer sr-only"
                              />
                              <div className="peer h-5 w-9 rounded-full bg-[var(--line)] after:absolute after:left-[2px] after:top-[2px] after:h-4 after:w-4 after:rounded-full after:bg-[var(--surface)] after:transition-all after:content-[''] peer-checked:bg-[var(--accent)] peer-checked:after:translate-x-full peer-focus:outline-none" />
                            </label>
                          </span>
                        </div>
                      );
                    })}

                    {pluginMenuView === "active" && activeTools.length === 0 ? (
                      <div className="px-2 py-6 text-center text-sm text-[var(--muted)]">{t("No active tools")}</div>
                    ) : null}
                    {pluginMenuView === "add" && inactiveTools.length === 0 ? (
                      <div className="px-2 py-6 text-center text-sm text-[var(--muted)]">{t("All tools are active")}</div>
                    ) : null}
                  </div>

                  {pluginMenuView === "active" && inactiveTools.length > 0 ? (
                    <button
                      type="button"
                      className="mt-2 flex h-10 w-full items-center justify-center gap-2 rounded-full border border-[var(--line)] bg-[var(--surface-2)] text-sm font-medium hover:bg-[var(--surface-3)]"
                      onClick={() => setPluginMenuView("add")}
                    >
                      <Plus size={15} />
                      {t("Add tool")}
                    </button>
                  ) : null}
                </div>
              )}
            </div>

            {composerStatus ? (
              <div
                className={[
                  "mt-2 break-words px-3 font-mono text-xs sm:truncate sm:px-4",
                  composerStatusIsReconnect ? "text-[var(--red)]" : "text-[var(--muted)]",
                ].join(" ")}
              >
                {composerStatus}
              </div>
            ) : null}
          </div>
        </footer>
          </>
        )}
      </main>

      <AgentPickerModal
        open={agentPickerOpen}
        mode={agentPickerMode}
        agents={agents}
        onClose={() => setAgentPickerOpen(false)}
        onSelect={chooseAgentForStart}
      />

      {networkOpen ? (
        <>
          <button
            type="button"
            aria-label={t("Close")}
            className="fixed inset-0 z-40 cursor-default"
            onClick={() => setNetworkOpen(false)}
          />
          <div className="fixed left-1/2 top-16 z-50 w-[calc(100vw-1.5rem)] max-w-sm -translate-x-1/2 rounded-2xl border border-[var(--line)] bg-[var(--surface)] p-4 shadow-popover">
            <div className="flex items-center gap-2.5">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-[var(--accent-soft)] text-[var(--accent)]">
                <Smartphone size={16} />
              </span>
              <div className="leading-tight">
                <div className="text-sm font-semibold text-[var(--ink)]">{t("Open on your phone")}</div>
                <div className="text-xs text-[var(--muted)]">{t("On the same Wi-Fi as this Mac.")}</div>
              </div>
            </div>

            <div className="mt-3 flex items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--ink)]">{networkPrimaryUrl}</span>
              <button
                type="button"
                onClick={() => void copyNetworkLink(networkPrimaryUrl)}
                className="shrink-0 rounded-full p-1.5 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                title={t("Copy")}
                aria-label={t("Copy")}
              >
                {networkCopied === networkPrimaryUrl ? <Check size={15} className="text-[var(--green)]" /> : <Copy size={15} />}
              </button>
            </div>

            {networkSecondaryUrl ? (
              <div className="mt-2 truncate font-mono text-[0.68rem] text-[var(--muted)]">{t("or")} {networkSecondaryUrl}</div>
            ) : null}

            {networkInfo && !networkInfo.lan_active ? (
              <p className="mt-3 rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2 text-xs leading-relaxed text-[var(--muted)]">
                {t('Sammy isn\'t sharing on the network yet. Run "sammy lan" in Terminal, then reopen this.')}
              </p>
            ) : null}

            <div className="mt-4 border-t border-[var(--line)] pt-3">
              <div className="flex items-center gap-2 text-xs font-semibold text-[var(--ink)]">
                <Globe size={14} className="text-[var(--accent)]" />
                {t("From any network")}
              </div>
              {networkTailnetUrl ? (
                <>
                  <div className="mt-2 flex items-center gap-2 rounded-xl border border-[var(--line)] bg-[var(--inset)] px-3 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--ink)]">{networkTailnetUrl}</span>
                    <button
                      type="button"
                      onClick={() => void copyNetworkLink(networkTailnetUrl)}
                      className="shrink-0 rounded-full p-1.5 text-[var(--muted)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"
                      title={t("Copy")}
                      aria-label={t("Copy")}
                    >
                      {networkCopied === networkTailnetUrl ? <Check size={15} className="text-[var(--green)]" /> : <Copy size={15} />}
                    </button>
                  </div>
                  {networkTailnetSecure ? (
                    <p className="mt-1.5 text-xs text-[var(--green)]">{t("Secure (HTTPS) — works on cellular too.")}</p>
                  ) : (
                    <p className="mt-1.5 text-xs leading-relaxed text-[var(--muted)]">
                      {t("For a secure (https) link, enable HTTPS in the Tailscale admin, then run \"sammy serve\".")}
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1.5 text-xs leading-relaxed text-[var(--muted)]">
                  {t("Install Tailscale on this Mac and your phone (same account) to get a link that works on cellular and other Wi-Fi. It'll show up here automatically.")}
                </p>
              )}
            </div>
          </div>
        </>
      ) : null}

      <BestiePanel
        open={bestieOpen}
        onClose={() => setBestieOpen(false)}
        besties={besties}
        activeBestieId={settings.active_bestie_id}
        geminiConfigured={settings.gemini_configured}
        onSelect={(bestieId) => updateActiveBestie(bestieId)}
        onRefresh={refreshBesties}
        onConnectGemini={() => {
          setBestieOpen(false);
          openSettings("security", "gemini");
        }}
        setStatus={setStatus}
      />

      <SettingsPanel
        open={settingsOpen}
        onClose={() => {
          setSettingsOpen(false);
          setSettingsFocusTarget(null);
        }}
        initialTab={settingsInitialTab}
        initialFocusTarget={settingsFocusTarget}
        settings={settings}
        setSettings={(nextSettings) => {
          setSettings(nextSettings);
          setAuthStatus({
            password_required: nextSettings.access_password_enabled,
            authenticated: true,
          });
          setSelectedModel(resolveModelName(nextSettings.default_model, models));
        }}
        agents={agents}
        setAgents={setAgents}
        tools={tools}
        refreshTools={refreshTools}
        models={models}
      />

      {!authStatus.password_required ? <PasswordSetupModal onSubmit={setInitialPassword} /> : null}
    </div>
    </IdentityContext.Provider>
  );
}
