import { createRouter, createWebHistory } from 'vue-router'
import Home from './views/Home.vue'
import Fold from './views/Fold.vue'
import Mutate from './views/Mutate.vue'
import Tertiary from './views/Tertiary.vue'
import Deep from './views/Deep.vue'

const routes = [
  { path: '/', name: 'home', component: Home },
  { path: '/fold', name: 'fold', component: Fold },
  { path: '/mutate', name: 'mutate', component: Mutate },
  { path: '/tertiary', name: 'tertiary', component: Tertiary },
  { path: '/deep', name: 'deep', component: Deep },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})