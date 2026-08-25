import { expect, test } from '@playwright/test'

test('研究闭环：看板到详情、自选、提醒与报告', async ({ page }, testInfo) => {
  const consoleErrors: string[] = []
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })
  await page.goto('/')
  await expect(page.getByText('交易日 2025-03-31')).toBeVisible()
  await expect(page.getByRole('region', { name: '主要指数' })).toContainText('上证指数')
  await expect(page.getByText('600000', { exact: true })).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('dashboard.png'), fullPage: true })

  await page.getByText('600000', { exact: true }).click()
  await expect(page.getByRole('heading', { name: '示例股份001' })).toBeVisible()
  await expect(page.getByLabel('K线与技术指标图')).toBeVisible()
  const noteText = '等待量价结构继续确认'
  if ((await page.getByLabel('笔记内容').count()) === 0) {
    await page.getByLabel('观察结论').fill(noteText)
    await page.getByRole('button', { name: '保存笔记' }).click()
  }
  await expect(page.getByLabel('笔记内容')).toHaveValue(noteText)

  await page.getByRole('link', { name: '股票筛选' }).click()
  await page.getByRole('button', { name: '执行筛选' }).click()
  await expect(page.getByText('筛选结果')).toBeVisible()

  await page.getByRole('link', { name: '自选股' }).click()
  if ((await page.getByText('SH 600000').count()) === 0) {
    await page.getByLabel('股票代码').fill('600000')
    await page.getByRole('button', { name: '加入自选' }).click()
  }
  await expect(page.getByText('SH 600000')).toBeVisible()

  await page.getByRole('link', { name: '行情看板' }).click()
  const confirm = page.getByRole('button', { name: '确认提醒' })
  if (await confirm.isVisible()) await confirm.click()

  await page.getByRole('link', { name: '分析报告' }).click()
  await page.getByLabel('股票代码').fill('600000')
  await page.getByRole('button', { name: '生成报告' }).click()
  await expect(page.getByText(/## 数据口径与完整性/)).toBeVisible()
  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('link', { name: '导出 Markdown' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/^2025-03-31-600000-\d+\.md$/)
  await page.screenshot({ path: testInfo.outputPath('report.png'), fullPage: true })

  expect(consoleErrors).toEqual([])
})
