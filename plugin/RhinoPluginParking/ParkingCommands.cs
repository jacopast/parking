using System.Runtime.InteropServices;
using Rhino;
using Rhino.Commands;
using Rhino.UI;

namespace RhinoPluginParking;

[Guid("6D38F442-8B25-46B3-92D2-F07D1728EEDD")]
[CommandStyle(Style.ScriptRunner)]
public sealed class ParkingFeasibilityCommand : Command
{
    public override string EnglishName => "ParkingFeasibility";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        return EmbeddedPythonRunner.Run("parking_feasibility_panel.py")
            ? Result.Success
            : Result.Failure;
    }
}

[Guid("814F4F1E-A57B-4170-95EA-44BC7B3D11EA")]
[CommandStyle(Style.ScriptRunner)]
public sealed class ParkingLayoutCommand : Command
{
    public override string EnglishName => "ParkingLayout";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        return EmbeddedPythonRunner.Run("parking_layout.py")
            ? Result.Success
            : Result.Failure;
    }
}

[Guid("1AAEBED1-248C-43A7-B041-43A56F0823E9")]
public sealed class ParkingToolsCommand : Command
{
    public override string EnglishName => "ParkingTools";

    protected override Result RunCommand(RhinoDoc doc, RunMode mode)
    {
        Panels.OpenPanel(ParkingPanel.PanelId);
        return Result.Success;
    }
}
