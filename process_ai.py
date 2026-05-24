import pandas as pd

def generate_report():
    try:
        # 讀取 Excel
        df = pd.read_excel('data.xlsx')
        
        # 1. 族群分析 (計算每個分類出現的次數)
        all_tags = df['分類'].str.split(',', expand=True).stack()
        group_analysis = all_tags.value_counts().reset_index()
        group_analysis.columns = ['族群名稱', '數量']
        
        # 2. 分數分析 (計算每個分類的平均分數)
        results = []
        unique_tags = all_tags.unique()
        for cat in unique_tags:
            avg_val = df[df['分類'].str.contains(cat)]['分數'].mean()
            results.append({'族群名稱': cat, '平均分數': round(avg_val, 2)})
        score_analysis = pd.DataFrame(results)
        
        # 3. 合併結果
        final_df = pd.merge(group_analysis, score_analysis, on='族群名稱')
        
        # 4. 產生 HTML (加入兩段分析與 CSS 美化)
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: sans-serif; margin: 20px; }}
                table {{ border-collapse: collapse; width: 100%; margin-bottom: 30px; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #f2f2f2; }}
                h2 {{ color: #333; }}
            </style>
        </head>
        <body>
            <h1>綜合分析報告</h1>
            <h2>一、族群結構與表現分析</h2>
            {final_df.to_html(index=False, border=0)}
        </body>
        </html>
        """
        
        with open('report.html', 'w', encoding='utf-8') as f:
            f.write(html_content)
            
        print("報表已生成：report.html (包含族群與分數統計)")
        
    except Exception as e:
        print(f"程式執行錯誤：{e}")

if __name__ == "__main__":
    generate_report()
