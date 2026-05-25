<template>
  <n-config-provider :locale="naiveLocale" :date-locale="naiveDateLocale" :theme-overrides="themeOverrides">
    <n-notification-provider>
      <n-message-provider>
        <n-dialog-provider>
          <n-layout has-sider style="height: 100vh">
            <n-layout-sider bordered :width="220" :collapsed-width="64" show-trigger collapse-mode="width" :collapsed="collapsed" @update:collapsed="v => collapsed = v">
              <div class="brand">
                <img v-if="!collapsed" src="/logo.svg" alt="TradingAgents-Studio" class="brand-logo-full" />
                <img v-else src="/favicon.svg" alt="TA" class="brand-logo-mark" />
              </div>
              <n-menu :options="menuOptions" :value="currentKey" @update:value="onMenuSelect" />
              <div class="sidebar-footer">
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button quaternary circle @click="toggleLocale">
                      <template #icon>
                        <n-icon><LanguageOutline /></n-icon>
                      </template>
                      <span v-if="!collapsed" style="margin-left: 4px; font-size: 12px">
                        {{ locale === 'zh-CN' ? 'EN' : '中' }}
                      </span>
                    </n-button>
                  </template>
                  {{ t('app.switchLanguage') }}
                </n-tooltip>
                <n-tooltip trigger="hover">
                  <template #trigger>
                    <n-button quaternary circle @click="toggleThemeColor">
                      <template #icon>
                        <n-icon :color="isRedTheme ? '#d03050' : '#18a058'">
                          <ColorPaletteOutline />
                        </n-icon>
                      </template>
                    </n-button>
                  </template>
                  {{ isRedTheme ? t('app.themeRed') : t('app.themeGreen') }}
                </n-tooltip>
              </div>
            </n-layout-sider>
            <n-layout-content style="padding: 24px; overflow-y: auto">
              <router-view />
            </n-layout-content>
          </n-layout>
        </n-dialog-provider>
      </n-message-provider>
    </n-notification-provider>
  </n-config-provider>
</template>

<script setup lang="ts">
import { computed, h, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter, useRoute } from 'vue-router'
import { NIcon } from 'naive-ui'
import {
  HomeOutline,
  AddCircleOutline,
  TimeOutline,
  SettingsOutline,
  ColorPaletteOutline,
  WalletOutline,
  AlarmOutline,
  TrendingUpOutline,
  StatsChartOutline,
  AnalyticsOutline,
  LanguageOutline,
} from '@vicons/ionicons5'
import { naiveLocale, naiveDateLocale, setLocale } from './i18n'

const { t, locale } = useI18n()
const router = useRouter()
const route = useRoute()

const collapsed = ref(false)
const isRedTheme = ref(localStorage.getItem('themeColor') !== 'green')

function toggleThemeColor() {
  isRedTheme.value = !isRedTheme.value
  localStorage.setItem('themeColor', isRedTheme.value ? 'red' : 'green')
}

function toggleLocale() {
  setLocale(locale.value === 'zh-CN' ? 'en-US' : 'zh-CN')
}

const themeOverrides = computed(() => ({
  common: {
    primaryColor: isRedTheme.value ? '#d03050' : '#18a058',
    primaryColorHover: isRedTheme.value ? '#de576d' : '#36ad6a',
    primaryColorPressed: isRedTheme.value ? '#ab1f3f' : '#0c7a43',
    primaryColorSuppl: isRedTheme.value ? '#de576d' : '#36ad6a',
  },
}))

const currentKey = computed(() => route.name as string)

function renderIcon(icon: any) {
  return () => h(NIcon, null, { default: () => h(icon) })
}

const menuOptions = computed(() => [
  { label: t('menu.dashboard'), key: 'dashboard', icon: renderIcon(HomeOutline) },
  { label: t('menu.analyze'), key: 'analyze', icon: renderIcon(AddCircleOutline) },
  { label: t('menu.holdings'), key: 'holdings', icon: renderIcon(WalletOutline) },
  { label: t('menu.schedule'), key: 'schedule', icon: renderIcon(AlarmOutline) },
  { label: t('menu.paper'), key: 'paper', icon: renderIcon(TrendingUpOutline) },
  { label: t('menu.backtest'), key: 'backtest', icon: renderIcon(StatsChartOutline) },
  { label: t('menu.quality'), key: 'quality', icon: renderIcon(AnalyticsOutline) },
  { label: t('menu.history'), key: 'history', icon: renderIcon(TimeOutline) },
  { label: t('menu.settings'), key: 'settings', icon: renderIcon(SettingsOutline) },
])

function onMenuSelect(key: string) {
  router.push({ name: key })
}
</script>

<style>
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
.brand {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 14px 12px 10px;
  border-bottom: 1px solid #f0f0f5;
}
.brand-logo-full {
  height: 36px;
  width: auto;
  display: block;
}
.brand-logo-mark {
  height: 30px;
  width: 30px;
  display: block;
}
.sidebar-footer {
  position: absolute;
  bottom: 16px;
  left: 0;
  right: 0;
  display: flex;
  justify-content: center;
  gap: 6px;
}
</style>
