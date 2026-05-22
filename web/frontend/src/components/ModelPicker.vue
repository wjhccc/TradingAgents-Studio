<template>
  <div class="model-picker">
    <!-- No static catalog for this provider (or none selected): plain input. -->
    <n-input
      v-if="!catalogOptions.length"
      :value="modelValue"
      :placeholder="freeTextPlaceholderText"
      @update:value="onTextInput"
    />

    <template v-else>
      <n-select
        :value="selectValue"
        :options="selectOptions"
        :placeholder="placeholderText"
        @update:value="onSelectChange"
      />
      <!-- "Custom model ID" mode: free-text input below. -->
      <n-input
        v-if="selectValue === CUSTOM_VALUE"
        :value="modelValue === CUSTOM_VALUE ? '' : modelValue"
        :placeholder="customPlaceholderText"
        style="margin-top: 6px"
        @update:value="onTextInput"
      />
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ModelOption } from '../stores/settings'

const CUSTOM_VALUE = 'custom'

const { t } = useI18n()

const props = defineProps<{
  modelValue: string
  options: ModelOption[]
  /** What to show in the dropdown placeholder. */
  placeholder?: string
  /** Placeholder for the inline free-text input when "Custom model ID" is picked. */
  customPlaceholder?: string
  /** Placeholder when there's no catalog at all (e.g. openrouter / azure). */
  freeTextPlaceholder?: string
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: string): void
}>()

const placeholderText = computed(() => props.placeholder ?? t('model.selectPlaceholder'))
const customPlaceholderText = computed(() => props.customPlaceholder ?? t('model.customPlaceholder'))
const freeTextPlaceholderText = computed(() => props.freeTextPlaceholder ?? t('model.freeTextPlaceholder'))

const catalogOptions = computed(() => props.options || [])

/** Map catalog options to n-select format. */
const selectOptions = computed(() =>
  catalogOptions.value.map(o => ({ label: o.label, value: o.value }))
)

/** Which option is currently "selected" in the dropdown.
 *
 *  Three cases:
 *   - modelValue is one of the catalog values → that one
 *   - modelValue is empty → null (placeholder visible)
 *   - modelValue is set but not in catalog → "custom" (and we show the
 *     inline text input pre-filled with the value)
 */
const selectValue = computed<string | null>(() => {
  const v = props.modelValue
  if (!v) return null
  if (catalogOptions.value.some(o => o.value === v)) return v
  if (catalogOptions.value.some(o => o.value === CUSTOM_VALUE)) return CUSTOM_VALUE
  // Catalog exists but doesn't have a "custom" slot — fall back to showing
  // the raw value as if it was picked (won't appear in the dropdown but the
  // visual stays consistent).
  return v
})

function onSelectChange(v: string | null) {
  if (v === CUSTOM_VALUE) {
    // Switching INTO custom mode: clear the value so the free-text input
    // starts empty. The user-facing label of the input prompts for an ID.
    emit('update:modelValue', '')
    return
  }
  emit('update:modelValue', v || '')
}

function onTextInput(v: string) {
  emit('update:modelValue', v)
}
</script>

<style scoped>
.model-picker {
  width: 100%;
}
</style>
