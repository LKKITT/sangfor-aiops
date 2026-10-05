import { describe, it, expect } from 'vitest'
import { validAddrField, validMembersField, validPortField, validMac } from '../validators'

// 校验器回归（HCI-2 的核心诉求）：非法输入提交前即时拦截，而非等设备报错。
// 口径：形似 IP/网段/范围/端口的项严格校验；纯文本视为地址组名/服务名放行。

const run = (fn, value) =>
  new Promise(resolve => fn({}, value, err => resolve(err ? err.message : null)))

describe('validAddrField（地址类字段）', () => {
  it('合法 IP / 网段 / 范围通过', async () => {
    expect(await run(validAddrField, '192.168.1.1')).toBeNull()
    expect(await run(validAddrField, '10.0.0.0/24')).toBeNull()
    expect(await run(validAddrField, '10.0.0.1-10.0.0.10')).toBeNull()
    expect(await run(validAddrField, '192.168.1.1, 10.0.0.0/8，全部')).toBeNull()
  })

  it('形似 IP 但非法的值被拦截（如 999.1.1.1）', async () => {
    expect(await run(validAddrField, '999.1.1.1')).toMatch('不是合法的 IP 地址')
    expect(await run(validAddrField, '192.168.1.256')).toMatch('不是合法的 IP 地址')
  })

  it('网段前缀越界被拦截，纯组名放行', async () => {
    expect(await run(validAddrField, '10.0.0.0/40')).toMatch('前缀长度')
    expect(await run(validAddrField, '总部IP组')).toBeNull()
  })
})

describe('validMembersField（对象成员：严格地址列表）', () => {
  it('合法成员列表通过', async () => {
    expect(await run(validMembersField, '10.0.0.0/24, 192.168.1.5, 10.0.0.1-10.0.0.10')).toBeNull()
  })

  it('组名/非法项被拦截（成员不允许组名）', async () => {
    expect(await run(validMembersField, '总部IP组')).toMatch('不是合法的 IP/网段/范围')
    expect(await run(validMembersField, '10.0.0.300')).toMatch('不是合法的')
    expect(await run(validMembersField, '')).toMatch('请填写')
  })
})

describe('validPortField（端口）', () => {
  it('合法端口/范围/列表通过', async () => {
    expect(await run(validPortField, '80')).toBeNull()
    expect(await run(validPortField, '8000-9000,9090')).toBeNull()
  })

  it('非法端口被拦截', async () => {
    expect(await run(validPortField, '0')).toMatch('端口范围不合法')
    expect(await run(validPortField, '70000')).toMatch('端口范围不合法')
    expect(await run(validPortField, '9000-8000')).toMatch('起始≤结束')
    expect(await run(validPortField, 'abc')).toMatch('端口格式')
  })
})

describe('validMac', () => {
  it('合法 MAC 通过（连字符/冒号）', async () => {
    expect(await run(validMac, '11-22-33-44-55-66')).toBeNull()
    expect(await run(validMac, '11:22:33:44:55:66')).toBeNull()
  })

  it('非法 MAC 被拦截', async () => {
    expect(await run(validMac, '112233445566')).toMatch('MAC 格式')
    expect(await run(validMac, '11-22-33-44-55')).toMatch('MAC 格式')
  })
})
