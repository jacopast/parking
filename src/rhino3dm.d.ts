declare module 'rhino3dm' {
  export type RhinoFactory = () => Promise<any>;

  const rhino3dm: RhinoFactory;
  export default rhino3dm;
}
