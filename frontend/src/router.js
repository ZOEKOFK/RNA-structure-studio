import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'

// 其余页面按需加载：ECharts 等较重依赖不会进入首屏
const routes = [
  { path: '/', name: 'home', component: Home },
  { path: '/fold', name: 'fold', component: () => import('./views/Fold.vue') },
  { path: '/mutate', name: 'mutate', component: () => import('./views/Mutate.vue') },
  { path: '/tertiary', name: 'tertiary', component: () => import('./views/Tertiary.vue') },
  { path: '/deep', name: 'deep', component: () => import('./views/Deep.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
