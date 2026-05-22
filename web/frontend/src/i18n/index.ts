import { createI18n } from 'vue-i18n'
import { zhCN, enUS, dateZhCN, dateEnUS } from 'naive-ui'
import type { NLocale, NDateLocale } from 'naive-ui'
import { computed } from 'vue'
import zh from './locales/zh-CN'
import en from './locales/en-US'

export type Locale = 'zh-CN' | 'en-US'

const STORAGE_KEY = 'locale'

function detectInitial(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY) as Locale | null
  if (saved === 'zh-CN' || saved === 'en-US') return saved
  // Fall back to browser preference: anything starting with "zh" → zh-CN.
  const nav = (navigator.language || '').toLowerCase()
  return nav.startsWith('zh') ? 'zh-CN' : 'en-US'
}

export const i18n = createI18n({
  legacy: false,
  locale: detectInitial(),
  fallbackLocale: 'zh-CN',
  messages: {
    'zh-CN': zh,
    'en-US': en,
  },
})

export function setLocale(loc: Locale) {
  i18n.global.locale.value = loc
  localStorage.setItem(STORAGE_KEY, loc)
  document.documentElement.setAttribute('lang', loc)
}

// Reactive Naive UI locale bundles derived from the current i18n locale.
const NAIVE_LOCALES: Record<Locale, { ui: NLocale; date: NDateLocale }> = {
  'zh-CN': { ui: zhCN, date: dateZhCN },
  'en-US': { ui: enUS, date: dateEnUS },
}

export const naiveLocale = computed(() => NAIVE_LOCALES[i18n.global.locale.value as Locale].ui)
export const naiveDateLocale = computed(() => NAIVE_LOCALES[i18n.global.locale.value as Locale].date)

export const currentLocale = computed<Locale>({
  get: () => i18n.global.locale.value as Locale,
  set: (v) => setLocale(v),
})

// Initialize <html lang="...">
document.documentElement.setAttribute('lang', i18n.global.locale.value as string)
