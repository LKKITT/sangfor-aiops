import { describe, it, expect } from 'vitest'
import { shortPort, shortPortPair } from '../portLabel'

describe('shortPort · 用户明确要求的样例', () => {
  it('GE1/0/11 → g0/0/11', () => {
    expect(shortPort('GE1/0/11')).toBe('g0/0/11')
  })
  it('XGE1/1 → XG0/0/1', () => {
    expect(shortPort('XGE1/1')).toBe('XG0/0/1')
  })
  it('GE1/0/24 → g0/0/24', () => {
    expect(shortPort('GE1/0/24')).toBe('g0/0/24')
  })
})

describe('shortPort · 全称缩写', () => {
  it('GigabitEthernet1/0/7 → g0/0/7', () => {
    expect(shortPort('GigabitEthernet1/0/7')).toBe('g0/0/7')
  })
  it('Ten-GigabitEthernet1/0/1 → XG0/0/1', () => {
    expect(shortPort('Ten-GigabitEthernet1/0/1')).toBe('XG0/0/1')
  })
  it('XGigabitEthernet1/0/1 → XG0/0/1', () => {
    expect(shortPort('XGigabitEthernet1/0/1')).toBe('XG0/0/1')
  })
  it('25GE1/1 → 25G0/0/1', () => {
    expect(shortPort('25GE1/1')).toBe('25G0/0/1')
  })
  it('100GE1/1 → 100G0/0/1', () => {
    expect(shortPort('100GE1/1')).toBe('100G0/0/1')
  })
  it('带空格的名称也能处理', () => {
    expect(shortPort('Gigabit Ethernet 1/0/1')).toBe('g0/0/1')
  })
})

describe('shortPort · 千兆与万兆可区分', () => {
  it('GE 前缀小写 g，XGE 保留大写 XG，视觉可辨', () => {
    expect(shortPort('GE1/0/1')).toMatch(/^g/)
    expect(shortPort('XGE1/0/1')).toMatch(/^XG/)
  })
})

describe('shortPort · 边界与容错', () => {
  it('空值返回空串', () => {
    expect(shortPort('')).toBe('')
    expect(shortPort(null)).toBe('')
    expect(shortPort(undefined)).toBe('')
  })
  it('纯数字端口原样返回（避免误改 VLAN）', () => {
    expect(shortPort('22')).toBe('22')
    expect(shortPort('1')).toBe('1')
  })
  it('无法识别的名称原样返回，不丢信息', () => {
    expect(shortPort('CPU')).toBe('CPU')
    expect(shortPort('Weird-Port-9')).toBe('Weird-Port-9')
  })
  it('四段接口名保留完整层级', () => {
    expect(shortPort('GE1/1/1/1')).toBe('g0/1/1/1')
  })
  it('两段接口名补齐槽位', () => {
    expect(shortPort('GE1/5')).toBe('g0/0/5')
  })
})

describe('shortPortPair · 链路两端展示', () => {
  it('两端都有效时用 ↔ 连接', () => {
    expect(shortPortPair('GE1/0/11', 'GE1/0/24')).toBe('g0/0/11 ↔ g0/0/24')
  })
  it('同端口不重复显示', () => {
    expect(shortPortPair('GE1/0/1', 'GE1/0/1')).toBe('g0/0/1')
  })
  it('单端有效时只显示有效端', () => {
    expect(shortPortPair('GE1/0/1', '')).toBe('g0/0/1')
    expect(shortPortPair('', 'GE1/0/2')).toBe('g0/0/2')
  })
  it('两端都无效时返回空串', () => {
    expect(shortPortPair('', '')).toBe('')
  })
})
