<script setup>
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <div class="page">
    <section class="card">
      <div class="empty">
        <span class="empty-icon">
          <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="9" />
            <path d="M9 10h.01M15 10h.01" stroke-width="2.4" />
            <path d="M9.5 15.5c.8-.9 1.7-1.3 2.5-1.3s1.7.4 2.5 1.3" />
          </svg>
        </span>
        <h1 class="nf-title">页面不存在</h1>
        <p>你访问的地址没有对应的功能页，可能输入有误或链接已过期。</p>
        <div class="nf-actions">
          <button @click="router.push('/')">返回首页</button>
          <button class="secondary" @click="router.push('/fold')">二级结构预测</button>
          <button class="secondary" @click="router.push('/tertiary')">3D 结构查看</button>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.nf-title {
  margin: 0 0 6px;
  font-size: 1.3rem;
  color: var(--text);
}
.nf-actions {
  display: flex;
  gap: 10px;
  justify-content: center;
  flex-wrap: wrap;
  margin-top: 18px;
}
</style>
