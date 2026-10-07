// 网络设备控制台命令速查字典（L1 本地补全：零延迟、离线可用）
// 点击命令仅插入终端，回车执行由操作者确认——安全边界：绝不自动执行。

const C = (cmd, desc) => ({ cmd, desc })

export const CONSOLE_COMMANDS = {
  huawei: [
    C('display version', '版本信息'),
    C('display device', '设备与单板状态'),
    C('display interface brief', '接口概览'),
    C('display ip interface brief', '三层接口与 IP'),
    C('display ip routing-table', '路由表'),
    C('display arp', 'ARP 表'),
    C('display mac-address', 'MAC 地址表'),
    C('display lldp neighbor brief', 'LLDP 邻居'),
    C('display current-configuration', '当前配置'),
    C('display logbuffer', '内存日志'),
    C('display cpu-usage', 'CPU 使用率'),
    C('display memory-usage', '内存使用率'),
    C('screen-length 0 temporary', '关闭分页'),
  ],
  h3c: [
    C('display version', '版本信息'),
    C('display device', '设备与单板状态'),
    C('display interface brief', '接口概览'),
    C('display ip interface brief', '三层接口与 IP'),
    C('display ip routing-table', '路由表'),
    C('display arp', 'ARP 表'),
    C('display mac-address', 'MAC 地址表'),
    C('display lldp neighbor-information list', 'LLDP 邻居'),
    C('display current-configuration', '当前配置'),
    C('display logbuffer', '内存日志'),
    C('display cpu-usage', 'CPU 使用率'),
    C('display memory', '内存使用率'),
    C('screen-length disable', '关闭分页'),
  ],
  cisco: [
    C('show version', '版本信息'),
    C('show interfaces status', '接口概览'),
    C('show ip interface brief', '三层接口与 IP'),
    C('show ip route', '路由表'),
    C('show arp', 'ARP 表'),
    C('show mac address-table', 'MAC 地址表'),
    C('show cdp neighbors', 'CDP 邻居'),
    C('show running-config', '当前配置'),
    C('show logging', '日志'),
    C('show processes cpu', 'CPU 使用率'),
    C('show memory summary', '内存使用率'),
    C('terminal length 0', '关闭分页'),
  ],
  ruijie: [
    C('show version', '版本信息'),
    C('show interfaces status', '接口概览'),
    C('show ip interface brief', '三层接口与 IP'),
    C('show ip route', '路由表'),
    C('show arp', 'ARP 表'),
    C('show mac-address-table', 'MAC 地址表'),
    C('show running-config', '当前配置'),
    C('show logging', '日志'),
    C('terminal length 0', '关闭分页'),
  ],
  zte: [
    C('show version-running', '版本信息'),
    C('show interface brief', '接口概览'),
    C('show ip route', '路由表'),
    C('show arp', 'ARP 表'),
    C('show mac', 'MAC 地址表'),
    C('show running-config', '当前配置'),
    C('terminal length 0', '关闭分页'),
  ],
  mikrotik: [
    C('/system resource print', '系统资源'),
    C('/system routerboard print', '主板信息'),
    C('/ip address print', 'IP 地址'),
    C('/ip route print', '路由表'),
    C('/ip arp print', 'ARP 表'),
    C('/interface print', '接口概览'),
    C('/log print', '日志'),
  ],
  generic: [
    C('show version', '版本信息'),
    C('show interfaces', '接口概览'),
    C('show ip route', '路由表'),
    C('show arp', 'ARP 表'),
    C('show running-config', '当前配置'),
    C('show log', '日志'),
  ],
}

/** 按厂家 + 关键字过滤命令（cmd 与 desc 双字段匹配；无厂家档案回退 generic）。 */
export function suggestCommands(vendor, query = '') {
  const key = (vendor || '').toLowerCase()
  const cmds = CONSOLE_COMMANDS[key] || CONSOLE_COMMANDS.generic
  const q = (query || '').trim().toLowerCase()
  if (!q) return cmds
  return cmds.filter(c => c.cmd.toLowerCase().includes(q) || (c.desc || '').toLowerCase().includes(q))
}
