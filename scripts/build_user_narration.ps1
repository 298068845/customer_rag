$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech

$outDir = Join-Path (Get-Location) 'output\bandicam_narrated\narration'
New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$lines = @(
    @{ Id = '01'; Text = '程序常驻系统托盘。右键即可快速打开货盘管理台、话术管理台，并执行订阅更新等常用操作。' },
    @{ Id = '02'; Text = '这是货盘 R A G 管理台。首页集中展示档案规模，并提供知识库问答入口。' },
    @{ Id = '03'; Text = '输入商品或业务问题后，系统会结合现有货盘语料检索并生成回答。' },
    @{ Id = '04'; Text = '语料库页面支持查看数据明细、来源和同步状态，便于统一维护货盘信息。' },
    @{ Id = '05'; Text = '导入页面支持文件上传和订阅更新，并可查看后台任务进度与处理结果。' },
    @{ Id = '06'; Text = '分类与标签能力用于约束检索范围，让不同业务场景的回答更准确。' },
    @{ Id = '07'; Text = '话术 R A G 管理台用于维护固定话术、实时问答和组合规则。' },
    @{ Id = '08'; Text = '接下来在微信文件传输助手中演示实际使用效果。' },
    @{ Id = '09'; Text = '输入关键词并按 Tab 键，缓存结果几乎即时返回，本次实际耗时零点零五秒。' },
    @{ Id = '10'; Text = '更换商品后再次检索，首次查询也只需零点五五秒，并直接生成可发送内容。' },
    @{ Id = '11'; Text = '波浪号用于唤起话术选择。输入序号即可快速采用对应结果。' },
    @{ Id = '12'; Text = '常用检索与固定话术可以组合使用，减少复制、切换和人工查找。' },
    @{ Id = '13'; Text = '查询结果可继续预览、选择并发送，让客服回复保持快速和一致。' }
)

$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice.SelectVoice('Microsoft Huihui Desktop')
$voice.Rate = 1
$voice.Volume = 100
foreach ($line in $lines) {
    $path = Join-Path $outDir ("{0}.wav" -f $line.Id)
    $voice.SetOutputToWaveFile($path)
    $voice.Speak($line.Text)
    $voice.SetOutputToNull()
}
$voice.Dispose()
