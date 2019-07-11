# coding: utf-8

import pcbnew
import wx
import wx.dataview
import os

import matplotlib.pyplot as plt
import numpy as np

import PySpice.Logging.Logging as Logging
logger = Logging.setup_logging()

from lyngspice import NgSpice

class PowerNetAnalyzerGui(wx.Frame):
    def __init__(self, parent, board):
        self.board = board

        wx.Frame.__init__(self, parent, title="Power Net Analyzer")
        self.panel = wx.Panel(self) 

        netcb_label = wx.StaticText(self.panel, label = "Select net to analyze")
        
        # get net names from the board
        board_nets = board.GetNetsByName()
        self.netnames = []
        self.nets = []
        for netname, net in board_nets.items():
            if (str(netname) == ""):
                continue
            self.netnames.append(str(netname))
            self.nets.append(net)
        
        # create a ComboBox for displaying net names
        netcb = wx.ComboBox(self.panel, choices=self.netnames)
        
        # sourcecb_label = wx.StaticText(self.panel, label = "Select a source")

        # self.sourcecb = wx.ComboBox(self.panel)
        # self.sourcecb.Disable()

        # list of drain pads
        self.pad_config = wx.dataview.DataViewListCtrl(self.panel, size=wx.Size(350, 120))
        self.pad_config.AppendTextColumn("Pad Name")
        self.pad_config.AppendTextColumn("Current Draw", mode=wx.dataview.DATAVIEW_CELL_EDITABLE)
        self.pad_config.AppendToggleColumn("Source")
        self.pad_config.Fit()

        # set source row
        self.source_row = -1

        self.start_button = wx.Button(self.panel, label="Start Analysis")
        self.start_button.Disable()
        
        # create a layout box and add the elements
        self.box = wx.BoxSizer(wx.VERTICAL)
        self.box.Add(netcb_label, proportion=0)
        self.box.Add(netcb, proportion=0)
        #self.box.Add(sourcecb_label, proportion=0)
        #self.box.Add(self.sourcecb, proportion=0)
        self.box.Add(self.pad_config, proportion=0)
        self.box.Add(self.start_button,  proportion=0)
        
        self.panel.SetSizer(self.box)
        self.Bind(wx.EVT_BUTTON, self.OnPress, id=self.start_button.GetId())
        self.Bind(wx.EVT_COMBOBOX, self.OnSelectNet, id=netcb.GetId())
        self.Bind(wx.dataview.EVT_DATAVIEW_ITEM_VALUE_CHANGED, self.OnSelectSource, id=self.pad_config.GetId())
        #self.Bind(wx.EVT_COMBOBOX, self.OnSelectSource, id=self.sourcecb.GetId())

    def OnSelectNet(self, event):
        # set the analysis net to the chosen net
        self.analysis_netname = self.netnames[event.GetSelection()]
        self.analysis_net = self.nets[event.GetSelection()]

        print("New Analysis Net: {}".format(self.analysis_netname))

        # delete all items from pad config
        self.pad_config.DeleteAllItems()
        self.source_row = -1
        self.start_button.Disable()

        # select all pads belonging to the chosen net
        pads = self.board.GetPads()
        if len(pads) > 0:
            self.analysis_padnames = []
            self.analysis_pads = []
            for pad in pads:
                if pad.GetNet().GetNetname() == self.analysis_netname:
                    # add this pad to the list of pads on the analysis net
                    self.analysis_pads.append(pad)

                    # get information about the pad and the parent module
                    pad_num = pad.GetPadName()
                    parent_ref = pad.GetParent().GetReference()

                    # create a pad name and put it in the padnames list
                    self.analysis_padnames.append("{}-Pad{}".format(parent_ref, pad_num))

                    # populate the pad config
                    self.pad_config.AppendItem([self.analysis_padnames[-1], "0", False])
                
        
    def OnSelectSource(self, event):
        # if event occured in the source selection column
        if event.GetColumn() == 2:
            row = self.pad_config.ItemToRow(event.GetItem())
            if self.source_row == row:
                self.source_row = -1
                self.start_button.Disable()
            elif self.source_row != -1:
                self.pad_config.SetToggleValue(False, self.source_row, 2)
                self.source_row = row
                self.start_button.Enable()
            else:
                self.source_row = row
                self.start_button.Enable()

    def OnPress(self, event):
        if self.source_row != -1:
            self.run_analysis()

    # determine if a point is inside a given polygon or not
    # Polygon is a list of (x,y) pairs.
    # copied from http://www.ariel.com.au/a/python-point-int-poly.html
    def point_inside_polygon(self,x,y,poly):

        n = len(poly)
        inside =False

        p1x,p1y = poly[0]
        for i in range(n+1):
            p2x,p2y = poly[i % n]
            if y > min(p1y,p2y):
                if y <= max(p1y,p2y):
                    if x <= max(p1x,p2x):
                        if p1y != p2y:
                            xinters = (y-p1y)*(p2x-p1x)/(p2y-p1y)+p1x
                        if p1x == p2x or x <= xinters:
                            inside = not inside
            p1x,p1y = p2x,p2y

        return inside

    def run_analysis(self):
        print("Running Analysis of net: {}".format(self.analysis_netname))

        # Get board bounding box in (units: nanometers)
        bounding_box = self.board.GetBoardEdgesBoundingBox()
        root_x = bounding_box.GetX()
        root_y = bounding_box.GetY()
        width = bounding_box.GetWidth()
        height = bounding_box.GetHeight()

        self.analysis_grid_spacing = 100000 # 100000 nm (0.1 mm, 10 nodes per mm)
        self.analysis_sheet_resistance = 0.0005 # 5milliohms/sq

        #print("    - Processing at most {} nodes".format((width*height)/(self.analysis_grid_spacing**2)))

        print("    - Isolating Tracks")
        analysis_tracks = self.board.TracksInNet(self.analysis_net.GetNet())
        #print(len(analysis_tracks))

        print("    - Creating nodes")

        analysis_nodes = []
        nodes_to_process = 0
        i=0
        for x in range(root_x, root_x + width, self.analysis_grid_spacing):
            line = []
            j=0
            for y in range(root_y, root_y + height, self.analysis_grid_spacing):
                test_point = pcbnew.wxPoint(x,y)
                node_name = "n{}x{}".format(i,j)
                node_on_net = False

                # check if node belongs to each pad
                for index, pad in enumerate(self.analysis_pads):
                    if pad.HitTest(test_point):
                        # Set node name to pad name if pad has nonzero current draw or is the source
                        if self.pad_config.GetTextValue(index, 1) != "0" or self.pad_config.GetToggleValue(index, 2):
                            node_name = self.pad_config.GetTextValue(index, 0)

                        node_on_net = True
                        nodes_to_process += 1
                        break

                # check if node belongs to each track
                if not node_on_net:
                    for track in analysis_tracks:
                        if track.HitTest(test_point):
                            node_on_net = True
                            nodes_to_process += 1
                            break

                # TODO: check if node belongs to each fill

                # add the node name to the list if it is in the net
                if node_on_net:
                    line.append(node_name)
                else:
                    line.append("")
                    
                j+=1

            analysis_nodes.append(line)
            i+=1

        print("    - Need to process {} nodes".format(nodes_to_process))

        print("    - Creating SPICE simulation")

        analysis_netlist = ['analysis']
        n=1
        for i in range(1, len(analysis_nodes)-1):
            for j in range(1, len(analysis_nodes[0])-1):
                if analysis_nodes[i][j] != "":
                    # connect to node to the right if it is on the net
                    if analysis_nodes[i+1][j] != "":
                        analysis_netlist.append("R{} {} {} {}".format(n, analysis_nodes[i][j], analysis_nodes[i+1][j], self.analysis_sheet_resistance))
                        n+=1
                    # connect to node to the bottom if it is on the net
                    if analysis_nodes[i][j+1] != "":
                        analysis_netlist.append("R{} {} {} {}".format(n, analysis_nodes[i][j], analysis_nodes[i][j+1], self.analysis_sheet_resistance))
                        n+=1
                    # connect to node to the left if it is on the net
                    if analysis_nodes[i-1][j] != "":
                        analysis_netlist.append("R{} {} {} {}".format(n, analysis_nodes[i][j], analysis_nodes[i-1][j], self.analysis_sheet_resistance))
                        n+=1
                    # connect to node to the top if it is on the net
                    if analysis_nodes[i][j-1] != "":
                        analysis_netlist.append("R{} {} {} {}".format(n, analysis_nodes[i][j], analysis_nodes[i][j-1], self.analysis_sheet_resistance))
                        n+=1
        
        analysis_netlist.append("V1 {} 0 3.3".format(self.pad_config.GetTextValue(self.source_row,0)))

        for i,pad in enumerate(self.analysis_padnames):
            if self.pad_config.GetTextValue(i,1) != "0":
                analysis_netlist.append("I{} {} 0 {}".format(i, self.pad_config.GetTextValue(i,0), self.pad_config.GetTextValue(i,1)))

        analysis_netlist.append(".op")
        analysis_netlist.append(".end")

        #print(analysis_netlist)

        print("    - Running SPICE simulation")
        ng = NgSpice()
        data,_ = ng.run(analysis_netlist)
        #print(data)

        node_voltages = []
        for i in range(len(analysis_nodes)):
            line = []
            for j in range(len(analysis_nodes[0])):
                if analysis_nodes[i][j] != "":
                    line.append(data["op1"][analysis_nodes[i][j].lower()][0])
                else:
                    line.append(0)
            node_voltages.append(line)

        plt.matshow(np.transpose(node_voltages))
        plt.show()



class SimplePlugin(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "Power Net Analyzer"
        self.category = "Analysis"
        self.description = "Analyzes power nets"
        self.show_toolbar_button = False # Optional, defaults to False
        #self.icon_file_name = os.path.join(os.path.dirname(__file__), 'simple_plugin.png') # Optional, defaults to ""

    def Run(self):
        # The entry function of the plugin that is executed on user action
        sg = PowerNetAnalyzerGui(None, pcbnew.GetBoard())
        sg.Show(True)

SimplePlugin().register() # Instantiate and register to Pcbnew