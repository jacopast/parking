using System.Reflection;
using Rhino;

namespace RhinoPluginParking;

internal static class EmbeddedPythonRunner
{
    private const string ResourcePrefix = "RhinoPluginParking.Scripts.";

    public static bool Run(string scriptFileName)
    {
        try
        {
            var scriptPath = Extract(scriptFileName);
            var escapedPath = scriptPath.Replace("\"", "\"\"");
            return RhinoApp.RunScript($"_-RunPythonScript \"{escapedPath}\"", false);
        }
        catch (Exception exception)
        {
            RhinoApp.WriteLine($"Parking plug-in error: {exception.Message}");
            return false;
        }
    }

    private static string Extract(string scriptFileName)
    {
        var assembly = Assembly.GetExecutingAssembly();
        var resourceName = ResourcePrefix + scriptFileName;

        using var resource = assembly.GetManifestResourceStream(resourceName)
            ?? throw new InvalidOperationException($"Embedded script not found: {resourceName}");

        var version = assembly.GetName().Version?.ToString() ?? "current";
        var scriptDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "RhinoPluginParking",
            version);
        Directory.CreateDirectory(scriptDirectory);

        var scriptPath = Path.Combine(scriptDirectory, scriptFileName);
        using var memory = new MemoryStream();
        resource.CopyTo(memory);
        var embeddedBytes = memory.ToArray();

        if (!File.Exists(scriptPath) || !File.ReadAllBytes(scriptPath).SequenceEqual(embeddedBytes))
            File.WriteAllBytes(scriptPath, embeddedBytes);

        return scriptPath;
    }
}
