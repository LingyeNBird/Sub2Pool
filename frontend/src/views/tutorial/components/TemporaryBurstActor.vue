<script setup lang="ts">
import AppIcon from "@/components/common/AppIcon.vue";

defineProps<{
  name: string;
  tone: "a" | "b" | "c" | "admin";
  caption?: string;
  value?: string;
  compact?: boolean;
}>();
</script>

<template>
  <div
    class="burst-actor"
    :class="[`actor-${tone}`, { 'actor-compact': compact }]"
  >
    <span class="actor-name">{{ name }}</span>
    <div class="actor-portrait" aria-hidden="true">
      <span class="actor-halo"></span>
      <AppIcon name="user" class="actor-person" />
      <AppIcon
        v-if="tone === 'admin'"
        name="shield-check"
        class="actor-shield"
      />
    </div>
    <span v-if="caption" class="actor-caption">{{ caption }}</span>
    <strong v-if="value" class="actor-value">{{ value }}</strong>
    <slot />
  </div>
</template>

<style scoped src="./TemporaryBurstActor.css"></style>
