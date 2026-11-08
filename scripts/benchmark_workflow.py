"""Reproducible 300-booth cold/warm timings; run the same script against a baseline checkout."""
import argparse,json,os,sys,time,statistics
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]));parser.add_argument('--output',required=True);parser.add_argument('--mode',choices=['normal','light'],default='normal');parser.add_argument('--repeats',type=int,default=3);args=parser.parse_args()
    sys.path.insert(0,str(Path(args.repo).resolve()))
    if sys.platform!='win32':os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QDate,QEvent,QCoreApplication
    from openpyxl import Workbook
    from prize_swap.ui import Window
    from prize_swap.floor_data import Machine
    from prize_swap.core import Product
    from prize_swap.sales_analysis import HEADERS,load_analysis
    from prize_swap.machine_matching import resolve_matches
    from prize_swap.instruction_preview import InstructionPreview
    from prize_swap.fonts import initialize_fonts
    app=QApplication.instance() or QApplication([]);initialize_fonts();result={'mode':args.mode,'platform':app.platformName(),'booths':300,'window':[1600,1000],'repeats':args.repeats,'metrics':{}}
    output=Path(args.output).resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(prefix='prize-benchmark-') as directory:
        tmp=Path(directory);w=Window(layout_storage=tmp/'floor.json');f=w.floor
        try:
            day=date(2026,11,8);w.day.setDate(QDate(day));f.follow_today=False;f.reference_date.setDate(QDate(day))
            ms=[Machine(number=f'{23000+i}右',source_key=f'R_{i}',product=f'計測景品{i:03} 270',kind=f'機種{i%3}',sales=(i+1)*100,average=(i+1)*100,ratio=30+(i%240),installed_on='2026-10-01',po_rate=.05,cost_rate=.6,dispensed=3,unit_price=270,x=i%20*240,y=i//20*170,width=220,height=150) for i in range(300)]
            book=Workbook();sheet=book.active;sheet.title='景品運営分析表'
            for column,label in HEADERS.items():sheet.cell(1,column+1,label)
            for m in ms:
                sale=m.sales;sheet.append([None,m.number.replace('右','-02'),m.kind,None,'売上登録用',sale,1,sale,sale*.4,.6,.05,3,sale/3,None,270,sale*.6])
            sheet.append(['集計期間：2026/11/06 ～ 2026/11/06']);path=tmp/'sales.xlsx';book.save(path)
            f.machines=ms;w.products=[Product(day,f'投入予定{i}',40,1,machines=[m.number],order=i) for i,m in enumerate(ms[:24])];w.refresh();w.resize(1600,1000);w.navigation.activate('floor');w.show();app.processEvents()
            mode_button=getattr(f,'render_mode',None)
            if mode_button is not None:mode_button.setCurrentIndex(mode_button.findData(args.mode))
            elif args.mode!='normal':raise RuntimeError('Baseline does not have light mode.')
            def measure(key,fn):
                values=[]
                for _ in range(args.repeats):
                    start=time.perf_counter();fn();app.processEvents();values.append((time.perf_counter()-start)*1000)
                result['metrics'][key]={'first_ms':round(values[0],3),'median_ms':round(statistics.median(values),3),'samples_ms':[round(v,3) for v in values]}
            measure('excel_read',lambda:load_analysis(path))
            snapshot=load_analysis(path)
            measure('matching',lambda:resolve_matches(ms,snapshot['records']))
            measure('initial_layout',lambda:(f.rebuild(),f.fit(),f.view.grab()))
            index=[0]
            def switch():
                index[0]+=1;f.set_color_filter('sales_metric' if index[0]%2 else 'ratio');f.view.grab()
            measure('filter_switch',switch)
            def select():
                index[0]+=1;f.selection_state.set(m.uid for m in ms[:(20 if index[0]%2 else 300)]);f.view.grab()
            measure('selection',select)
            def details():
                index[0]+=1;f.open_details(ms[index[0]%24].uid);f.detail_controller.dock.grab()
            measure('machine_detail',details);f.detail_controller.dock.close()
            f.instruction_print_options['context']=True
            w.navigation.activate('instructions');w.instruction_tabs.setCurrentWidget(w.instruction_map)
            measure('plan_map_display',lambda:(w.instruction_map.refresh(),w.instruction_map.view.grab()))
            measure('auto_fit',lambda:(w.instruction_map.fit(),w.instruction_map.view.grab()))
            def preview():
                d=InstructionPreview(w);d.show();app.processEvents();d.view.grab();d.reject();d.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            measure('pdf_preview',preview)
            w.navigation.activate('floor');f.fit();app.processEvents()
            measure('paint_viewport',lambda:f.view.grab())
            result.update(node_count=len(f.nodes),analytics_builds=f.analytics.builds)
        finally:
            w.dirty=False;w.instruction_save_timer.stop();f.timer.stop();f.day_timer.stop();w.preflight.timer.stop();w.close()
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(str(output));return 0

if __name__=='__main__':raise SystemExit(main())
