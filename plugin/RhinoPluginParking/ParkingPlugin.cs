using System.Drawing;
using System.Runtime.InteropServices;
using Rhino.PlugIns;
using Rhino.UI;

namespace RhinoPluginParking;

[Guid("375F83F7-2FD5-4D77-A22F-182CC4924FA1")]
public sealed class ParkingPlugin : PlugIn
{
    public static ParkingPlugin? Instance { get; private set; }

    public ParkingPlugin()
    {
        Instance = this;
    }

    public override PlugInLoadTime LoadTime => PlugInLoadTime.AtStartup;

    protected override LoadReturnCode OnLoad(ref string errorMessage)
    {
        Panels.RegisterPanel(
            this,
            typeof(ParkingPanel),
            "Parking Feasibility",
            SystemIcons.Application,
            PanelType.System);

        return LoadReturnCode.Success;
    }
}
