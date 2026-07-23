# iFinD企业报告结构化入库工具

这个项目用于把同花顺 iFinD 企业库导出的 PDF 企业分析报告转换为结构化数据，并保存到 SQLite 数据库中，方便后续风险量化程序调用。

## 当前能力

- 批量扫描指定目录下的 PDF 报告
- 抽取报告首页企业名称、报告生成时间、页数、文件哈希
- 保存全文文本和按章节切分的原文
- 抽取部分稳定字段：
  - 工商基础信息
  - 股东信息
  - 主要人员/核心团队线索
  - 融资事件
  - 招标公告/中标公告
  - 客户/供应商
  - 新闻舆情
  - 专利
  - 软件著作权
  - 商标
  - 司法/处罚等风险章节原文
- 对关键但未抽取到的信息写入 `missing_fields` 表，字段值为 `数据缺失`
- 使用 PDF 表格单元格优先解析专利表，减少专利号、日期、法律状态被换行截断的问题
- 生成公司级统计表 `company_statistics`，汇总专利数量、法律案件数量、新闻舆情数量和比例、主要股东及对外投资线索

## 使用方式

```powershell
cd "项目目录"
python -m ifind_report_parser.cli --source "报告PDF所在文件夹" --db ".\data\ifind_reports.db"
```

也可以不写 `--source`，运行后在控制台粘贴报告所在文件夹路径：

```powershell
python -m ifind_report_parser.cli --db ".\data\ifind_reports.db"
```

程序会递归扫描该文件夹及子文件夹下的所有 `.pdf` 文件。

## 在 PyCharm 中文界面中运行

1. 打开项目目录：

   `文件` → `打开` → 选择本项目目录。

2. 设置源码根目录：

   在左侧项目树右键 `src` → `将目录标记为` → `源根`。

3. 创建运行配置：

   `运行` → `编辑配置...` → 左上角 `+` → 选择 `Python`。

4. 固定报告目录的配置方式：

   - 名称：`解析iFinD企业报告`
   - 运行方式：选择 `模块名称`
   - 模块名称：`ifind_report_parser.cli`
   - 形参/参数：

     ```text
     --source "报告PDF所在文件夹" --db ".\data\ifind_reports.db"
     ```

   - 工作目录：

     ```text
     项目目录
     ```

5. 每次运行时手动输入报告目录的配置方式：

   如果你希望每次运行时临时指定 PDF 所在文件夹，参数只填：

   ```text
   --db ".\data\ifind_reports.db"
   ```

   运行后控制台会提示：

   ```text
   请输入iFinD企业报告PDF所在文件夹路径：
   ```

   此时粘贴报告文件夹路径即可。

6. 查看运行结果：

   运行完成后，数据库文件在：

   ```text
   data\ifind_reports.db
   ```

   推荐优先查看 `company_statistics` 表，它是公司级汇总结果。

## 数据库说明

默认数据库文件：

`data/ifind_reports.db`

主要表：

- `documents`：PDF报告元信息和全文
- `sections`：按编号标题切分后的章节文本
- `company_profiles`：企业工商基础信息
- `shareholders`：股东信息
- `people`：人员信息
- `financing_events`：投融资事件
- `tenders`：招标/中标公告
- `customers`：客户信息
- `suppliers`：供应商信息
- `news_events`：新闻舆情
- `patents`：专利
- `software_copyrights`：软件著作权
- `trademarks`：商标
- `risk_raw_sections`：司法、经营风险等暂不稳定结构化的章节原文
- `missing_fields`：关键缺失字段
- `company_statistics`：公司级统计汇总，包括专利数量、法律案件数量、负面/非负面/未识别新闻数量及比例、主要股东、对外投资摘要、缺失信息摘要

## 设计原则

PDF表格存在跨页、换行、错位问题。本项目优先抽取稳定字段；对不稳定字段保留章节原文和页码证据，不强行伪结构化。专利表已优先使用 PDF 单元格解析；其他表仍以规则解析和原文兜底为主。后续可以继续在 `table_parser.py` 中增加股东、新闻、客户供应商等表格的单元格解析规则。
