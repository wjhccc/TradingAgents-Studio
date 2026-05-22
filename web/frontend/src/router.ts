import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('./pages/Dashboard.vue') },
  { path: '/analyze', name: 'analyze', component: () => import('./pages/NewAnalysis.vue') },
  { path: '/progress/:id', name: 'progress', component: () => import('./pages/AnalysisProgress.vue') },
  { path: '/holdings', name: 'holdings', component: () => import('./pages/Holdings.vue') },
  { path: '/schedule', name: 'schedule', component: () => import('./pages/Schedule.vue') },
  { path: '/paper', name: 'paper', component: () => import('./pages/Paper.vue') },
  { path: '/backtest', name: 'backtest', component: () => import('./pages/Backtest.vue') },
  { path: '/history', name: 'history', component: () => import('./pages/History.vue') },
  { path: '/report/:id', name: 'report', component: () => import('./pages/ReportDetail.vue') },
  { path: '/settings', name: 'settings', component: () => import('./pages/Settings.vue') },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
