<script setup lang="ts">
/** 档枢 ArcHub 品牌标记：档案卷宗 + 检索光点。纯 SVG，无外部依赖，明暗底通用。 */
withDefaults(defineProps<{ size?: number; mono?: boolean }>(), { size: 34, mono: false })
</script>

<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 48 48"
    role="img"
    aria-label="档枢 ArcHub"
    xmlns="http://www.w3.org/2000/svg"
  >
    <defs>
      <linearGradient id="arc-plate" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" :stop-color="mono ? '#ffffff' : '#123A5E'" />
        <stop offset="100%" :stop-color="mono ? '#ffffff' : '#0A2138'" />
      </linearGradient>
    </defs>
    <rect x="1" y="1" width="46" height="46" rx="12" fill="url(#arc-plate)" />
    <!-- 卷宗：折角文档 -->
    <path
      d="M13.5 12.5h13.2l7.3 7.3v15.7a2 2 0 0 1-2 2H13.5a2 2 0 0 1-2-2v-21a2 2 0 0 1 2-2Z"
      :fill="mono ? '#0E2A47' : '#FFFFFF'"
      fill-opacity="0.96"
    />
    <path d="M26.7 12.5v7.3h7.3" :fill="mono ? '#0E2A47' : '#E4ECF5'" />
    <!-- 著录线 -->
    <path
      d="M16 24h9M16 28h6"
      :stroke="mono ? '#0E2A47' : '#9DB4CB'"
      stroke-width="1.6"
      stroke-linecap="round"
    />
    <!-- 检索放大镜 -->
    <circle cx="29" cy="29.5" r="6.4" fill="none" :stroke="mono ? '#0E2A47' : '#C69A4B'" stroke-width="2.6" />
    <path
      d="M33.8 34.3 38.5 39"
      :stroke="mono ? '#0E2A47' : '#C69A4B'"
      stroke-width="2.8"
      stroke-linecap="round"
    />
  </svg>
</template>
