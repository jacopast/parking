using System.Runtime.InteropServices;
using Rhino;
using Rhino.UI;
using Drawing = Eto.Drawing;
using Forms = Eto.Forms;

namespace RhinoPluginParking;

[Guid("01308BBD-506B-4BF5-BD78-95CE641C7FDE")]
public sealed class ParkingPanel : Forms.Panel, IPanel
{
    public static Guid PanelId => typeof(ParkingPanel).GUID;

    public ParkingPanel()
    {
        var title = new Forms.Label
        {
            Text = "Parking Feasibility",
            Font = Drawing.SystemFonts.Bold()
        };

        var description = new Forms.Label
        {
            Text = "Generate fixed 9' x 18' parking layouts with 24' drive aisles.",
            Wrap = Forms.WrapMode.Word
        };

        var feasibilityButton = new Forms.Button
        {
            Text = "Open Feasibility Generator"
        };
        feasibilityButton.Click += (_, _) => RunCommand("ParkingFeasibility");

        var layoutButton = new Forms.Button
        {
            Text = "Run Direct Layout"
        };
        layoutButton.Click += (_, _) => RunCommand("ParkingLayout");

        var layout = new Forms.DynamicLayout
        {
            Padding = new Drawing.Padding(12),
            DefaultSpacing = new Drawing.Size(8, 8)
        };
        layout.AddRow(title);
        layout.AddRow(description);
        layout.AddRow(null);
        layout.AddRow(feasibilityButton);
        layout.AddRow(layoutButton);
        layout.Add(null);
        Content = layout;
    }

    private static void RunCommand(string commandName)
    {
        if (!RhinoApp.RunScript("_" + commandName, false))
            Dialogs.ShowMessage($"Could not start {commandName}.", "Parking Feasibility");
    }

    public void PanelShown(uint documentSerialNumber, ShowPanelReason reason)
    {
    }

    public void PanelHidden(uint documentSerialNumber, ShowPanelReason reason)
    {
    }

    public void PanelClosing(uint documentSerialNumber, bool onCloseDocument)
    {
    }
}
