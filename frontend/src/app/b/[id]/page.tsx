import BusinessView from "@/components/BusinessView";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <BusinessView id={id} />;
}
