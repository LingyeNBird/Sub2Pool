<script setup lang="ts">
import type { Participant } from "@/types";

import RecommendationCard from "./RecommendationCard.vue";

defineProps<{
  participants: Participant[];
  appliedParticipantIds: number[];
}>();

defineEmits<{
  select: [participant: Participant];
}>();
</script>

<template>
  <section class="card col-span-12 bg-base-200 shadow-xs">
    <div class="card-body gap-5">
      <h2 class="card-title text-xl">
        <AppIcon name="sparkles" class="size-6" />
        当前额度建议
      </h2>
      <div v-if="participants.length" class="grid gap-4">
        <RecommendationCard
          v-for="participant in participants"
          :key="participant.id"
          :participant="participant"
          :applied="appliedParticipantIds.includes(participant.id)"
          @select="$emit('select', $event)"
        />
      </div>
      <div v-else class="py-6 text-center opacity-60">
        当前没有可展示的额度建议。
      </div>
    </div>
  </section>
</template>
