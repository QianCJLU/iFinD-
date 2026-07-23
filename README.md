# iFinD企业报告结构化入库工具
这个项目用于把同花顺 iFinD 企业库导出的 PDF 企业分析报告转换为结构化数据，并保存到 SQLite 数据库中，方便后续风险量化程序调用。
## 在 PyCharm 中文界面中运行
打开项目目录：

文件 → 打开 → 选择本项目目录。

设置源码根目录：

在左侧项目树右键 src → 将目录标记为 → 源根。

创建运行配置：

运行 → 编辑配置... → 左上角 + → 选择 Python。

固定报告目录的配置方式：

名称：解析iFinD企业报告

运行方式：选择 模块名称

模块名称：ifind_report_parser.cli

形参/参数：

--source "报告PDF所在文件夹" --db ".\data\ifind_reports.db"
工作目录：

项目目录
每次运行时手动输入报告目录的配置方式：

如果你希望每次运行时临时指定 PDF 所在文件夹，参数只填：

--db ".\data\ifind_reports.db"
运行后控制台会提示：

请输入iFinD企业报告PDF所在文件夹路径：
此时粘贴报告文件夹路径即可。

查看运行结果：

运行完成后，数据库文件在：

data\ifind_reports.db
推荐优先查看 company_statistics 表，它是公司级汇总结果。
